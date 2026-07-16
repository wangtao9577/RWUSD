from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Callable, Iterable
from inspect import signature

from src.domain.models import DEFAULT_SIM_LEVERAGE, PortfolioSnapshot, ProfitBucket
from src.domain.enums import PositionSide, StrategyPhase
from src.portfolio.transfers import RedeemPlan, SweepPlan
from src.portfolio.state import HedgeState
from src.backtest.datasource import BacktestRow
from src.exchange.binance_account import BinanceAccountService
from src.exchange.binance_stream import BinanceStreamClient
from src.infra.logging import InMemoryLogger
from src.portfolio.yield_accrual import accrue_rwusd_interest
from src.market.selector import SymbolSelector
from src.strategy.hedge_engine import HedgeEngine, StrategyIntent
from src.strategy.harvest_rules import HarvestRule
from src.strategy.dry_run_order_lifecycle import (
    DryRunMatchingEngine,
    DryRunOrderLifecycle,
    DryRunQuoteRuntime,
    DryRunTickResult,
)
from src.strategy.position_sizing import normalize_order_quantity
from src.strategy.pnl_manager import PnlManager
from src.strategy.rebalance import RebalancePlanner
from src.strategy.tick_quote_planner import TickQuotePlanner
from src.strategy.leg_route import (
    build_execution_preference,
    build_execution_route,
    derive_underlying_symbol,
    group_remote_hedges,
)


@dataclass(slots=True)
class LiveCycleResult:
    selected_symbol: str | None
    intent: StrategyIntent
    dry_run: bool
    risk_reason: str | None = None
    risk_should_pause: bool = False
    risk_should_reduce: bool = False


class LiveRunner:
    def __init__(
        self,
        account_service: BinanceAccountService,
        stream_client: BinanceStreamClient,
        candidate_symbols: list[str],
        target_notional: Decimal,
        long_take_profit: Decimal,
        short_take_profit: Decimal,
        dry_run: bool,
        state_store,
        risk_manager,
        transfer_planner,
        sim_leverage: Decimal = DEFAULT_SIM_LEVERAGE,
        switch_threshold: Decimal = Decimal("0.20"),
        logger=None,
        pnl_manager=None,
        rebalance_planner=None,
        bull_mode: bool = False,
        harvest_rule=None,
        rwusd_apr: Decimal = Decimal("0.12"),
        selector_eval_interval_minutes: int = 15,
        selector_switch_cooldown_minutes: int = 60,
        initial_profit_bucket: ProfitBucket | None = None,
        usdc_maker_enabled: bool = True,
        usdc_maker_allowed_phases: set[str] | None = None,
        usdc_maker_fallback_to_market_on_missing_price: bool = True,
        dry_run_order_lifecycle: DryRunOrderLifecycle | None = None,
        dry_run_matching_engine: DryRunMatchingEngine | None = None,
        dry_run_order_timeout_cycles: int = 0,
        dry_run_max_requotes: int = 0,
    ) -> None:
        self._account_service = account_service
        self._stream_client = stream_client
        self._candidate_symbols = set(candidate_symbols)
        self._route_symbols = set(candidate_symbols)
        self._target_notional = target_notional
        self._dry_run = dry_run
        self._state_store = state_store
        self._sim_leverage = sim_leverage
        self._risk_manager = risk_manager
        self._transfer_planner = transfer_planner
        self._logger = logger or InMemoryLogger()
        self._pnl_manager = pnl_manager or PnlManager(
            long_take_profit=long_take_profit,
            short_take_profit=short_take_profit,
        )
        self._rebalance_planner = rebalance_planner or RebalancePlanner()
        self._harvest_rule = harvest_rule or HarvestRule(
            taker_fee_bps=Decimal("5"),
            slippage_bps=Decimal("5"),
            min_net_pnl=Decimal("18"),
        )
        self._rwusd_apr = rwusd_apr
        self._selector_eval_interval_minutes = max(1, selector_eval_interval_minutes)
        self._bull_mode = bull_mode
        self._usdc_maker_enabled = usdc_maker_enabled
        self._usdc_maker_allowed_phases = set(
            usdc_maker_allowed_phases
            or {"open_hedge", "restore_now", "recover_missing_leg"}
        )
        self._usdc_maker_fallback_to_market_on_missing_price = (
            usdc_maker_fallback_to_market_on_missing_price
        )
        self._selector = SymbolSelector(
            switch_threshold=switch_threshold,
            eval_interval_minutes=self._selector_eval_interval_minutes,
            switch_cooldown_minutes=selector_switch_cooldown_minutes,
        )
        self._engine = HedgeEngine(
            target_notional=target_notional,
            long_take_profit=long_take_profit,
            short_take_profit=short_take_profit,
        )
        self._state = self._new_state()
        self._profit_bucket = initial_profit_bucket or ProfitBucket()
        self._failed_legs: dict[tuple[str, str], int] = {}
        self._latest_prices: dict[str, Decimal] = {}
        self._pending_rebalance_side: PositionSide | None = None
        self._restored_runtime_state = False
        self._dry_run_order_lifecycle = (
            dry_run_order_lifecycle
            if dry_run
            else None
        ) or (DryRunOrderLifecycle() if dry_run else None)
        self._dry_run_matching_engine = (
            dry_run_matching_engine if dry_run else None
        ) or (DryRunMatchingEngine() if dry_run else None)
        self._dry_run_order_timeout_cycles = max(0, dry_run_order_timeout_cycles)
        self._dry_run_max_requotes = max(0, dry_run_max_requotes)

    @property
    def symbol(self) -> str | None:
        return self._engine.symbol

    @property
    def phase(self) -> StrategyPhase:
        return self._engine.phase

    @property
    def profit_bucket(self) -> ProfitBucket:
        return self._profit_bucket

    @property
    def restored_runtime_state(self) -> bool:
        return self._restored_runtime_state

    @property
    def dry_run_order_lifecycle(self) -> DryRunOrderLifecycle | None:
        return self._dry_run_order_lifecycle

    def restore_state(self) -> None:
        runtime_state = self._load_runtime_state()
        if runtime_state is not None:
            profit_bucket = runtime_state.get("profit_bucket")
            if isinstance(profit_bucket, ProfitBucket):
                self._profit_bucket = profit_bucket
            pending_side = runtime_state.get("pending_rebalance_side")
            if isinstance(pending_side, PositionSide):
                self._pending_rebalance_side = pending_side
            self._restored_runtime_state = True

        self._restore_dry_run_order_lifecycle()

        restored = self._state_store.load_hedge_state()
        if restored is None:
            return

        self._state = restored
        if self._state.underlying_symbol is None and restored.symbol is not None:
            self._state.underlying_symbol = derive_underlying_symbol(restored.symbol)
        if self._state.long_symbol is None:
            self._state.long_symbol = restored.symbol
        if self._state.short_symbol is None:
            self._state.short_symbol = restored.symbol
        self._engine.symbol = restored.symbol
        self._engine.phase = restored.phase

    def run_dry_run_opening_tick(self, mid_price: Decimal) -> DryRunTickResult:
        runtime = self._dry_run_quote_runtime(mid_price)
        result = runtime.on_opening_tick(mid_price)
        self._sync_dry_run_lifecycle_state(mid_price)
        return result

    def run_dry_run_exit_tick(
        self,
        *,
        mid_price: Decimal,
        all_unrealized_pnl_realized: bool,
    ) -> DryRunTickResult:
        runtime = self._dry_run_quote_runtime(mid_price)
        result = runtime.on_exit_tick(
            mid_price=mid_price,
            all_unrealized_pnl_realized=all_unrealized_pnl_realized,
        )
        self._sync_dry_run_lifecycle_state(mid_price)
        return result

    def run_dry_run_hard_risk_tick(self, mid_price: Decimal) -> DryRunTickResult:
        runtime = self._dry_run_quote_runtime(mid_price)
        result = runtime.on_hard_risk_tick(mid_price)
        self._sync_dry_run_lifecycle_state(mid_price)
        return result

    def apply_dry_run_order_execution(
        self,
        *,
        order_id: str,
        cumulative_filled_quantity: Decimal,
        status: str,
        mark_price: Decimal,
    ):
        lifecycle = self._require_dry_run_order_lifecycle()
        order = lifecycle.apply_execution(
            order_id=order_id,
            cumulative_filled_quantity=cumulative_filled_quantity,
            status=status,
        )
        self._sync_dry_run_lifecycle_state(mark_price)
        return order

    def reconcile_remote_state(self) -> dict | None:
        remote_positions = [
            position
            for position in self._account_service.get_um_hedge_positions()
            if position.symbol in self._route_symbols
        ]

        if not remote_positions:
            if self._state.symbol is None:
                return None
            self._reset_state()
            return None

        remote_routes = group_remote_hedges(
            remote_positions,
            allowed_symbols=self._route_symbols,
        )
        if not remote_routes:
            if self._state.symbol is None:
                return None
            self._reset_state()
            return None

        if len(remote_routes) != 1:
            return {
                "action": "reconcile_required",
                "reason": "multiple_remote_symbols",
                "symbols": sorted(route.anchor_symbol for route in remote_routes),
            }

        remote_route = remote_routes[0]
        local_underlying = self._state.underlying_symbol
        if local_underlying is None and self._state.symbol is not None:
            local_underlying = derive_underlying_symbol(self._state.symbol)

        if local_underlying is not None and local_underlying != remote_route.underlying_symbol:
            return {
                "action": "reconcile_required",
                "reason": "symbol_mismatch",
                "local_symbol": self._state.symbol,
                "remote_symbol": remote_route.anchor_symbol,
            }

        self._restore_remote_hedge(remote_route)
        return None

    def run_cycle(
        self,
        rows: list[BacktestRow],
        snapshot: PortfolioSnapshot | None = None,
        current_drawdown: Decimal = Decimal("0"),
        minute_of_day: int | None = None,
        elapsed_hours: Decimal = Decimal("0"),
    ) -> LiveCycleResult:
        if self._profit_bucket.rwusd_principal > Decimal("0") and elapsed_hours > Decimal("0"):
            self._profit_bucket = accrue_rwusd_interest(
                bucket=self._profit_bucket,
                apr=self._rwusd_apr,
                elapsed_hours=elapsed_hours,
            )
            self._persist_state()

        risk_reason: str | None = None
        risk_should_pause = False
        risk_should_reduce = False
        if snapshot is not None:
            risk_decision = self._risk_manager.evaluate(
                snapshot=snapshot,
                current_drawdown=current_drawdown,
            )
            risk_reason = risk_decision.reason
            risk_should_pause = risk_decision.should_pause
            risk_should_reduce = risk_decision.should_reduce
            if (
                risk_reason is not None
                or risk_should_pause
                or risk_should_reduce
            ):
                self._log_risk_decision(
                    reason=risk_reason,
                    should_pause=risk_should_pause,
                    should_reduce=risk_should_reduce,
                    current_drawdown=current_drawdown,
                    snapshot=snapshot,
                )
            if risk_decision.should_pause:
                risk_mark_price = self._state.sim_last_mark_price
                if self._dry_run and risk_mark_price > Decimal("0"):
                    self._seed_dry_run_lifecycle_from_state()
                    self.run_dry_run_hard_risk_tick(risk_mark_price)
                    self._advance_dry_run_orders(risk_mark_price)
                self._engine.phase = StrategyPhase.PAUSED
                self._state.phase = StrategyPhase.PAUSED
                self._persist_state()
                return LiveCycleResult(
                    selected_symbol=None,
                    intent=StrategyIntent.hold(),
                    dry_run=self._dry_run,
                    risk_reason=risk_reason,
                    risk_should_pause=risk_should_pause,
                    risk_should_reduce=risk_should_reduce,
                )
            if risk_decision.should_redeem_topup:
                redeem_plan = self.plan_redeem_topup(snapshot=snapshot)
                self._log_redeem_topup_plan(
                    plan=redeem_plan,
                    snapshot=snapshot,
                )
                self._execute_redeem_topup(plan=redeem_plan, snapshot=snapshot)

        eligible_rows = self._enrich_selector_rows(
            [row for row in rows if row["symbol"] in self._candidate_symbols]
        )
        self._latest_prices.update({row["symbol"]: row["close"] for row in eligible_rows})
        if self._dry_run and self._engine.phase == StrategyPhase.OPENING_HEDGE:
            opening_price = self._reference_price_for_symbol(self._state.symbol or "")
            if opening_price is not None and opening_price > Decimal("0"):
                self._advance_dry_run_orders(opening_price, advance_cycle=True)
        self._mark_virtual_positions_from_rows(eligible_rows)
        selector_snapshot = self._select_symbol_snapshot(
            rows=eligible_rows,
            minute_of_day=minute_of_day,
        )
        self._log_selector_snapshot(selector_snapshot)
        allow_restore_now = self._is_eval_checkpoint(minute_of_day)
        cycle_intent: StrategyIntent | None = None
        take_profit_result = self._maybe_handle_take_profit(
            rows=eligible_rows,
            selected_symbol=selector_snapshot.selected_symbol,
            snapshot=snapshot,
            risk_should_pause=risk_should_pause,
            risk_should_reduce=risk_should_reduce,
            allow_restore_now=allow_restore_now,
        )
        if take_profit_result is not None:
            cycle_intent = take_profit_result

        if cycle_intent is None:
            continued_rebalance = self._maybe_continue_rebalancing(
                selected_symbol=selector_snapshot.selected_symbol,
                risk_should_pause=risk_should_pause,
                risk_should_reduce=risk_should_reduce,
                allow_restore_now=allow_restore_now,
            )
            if continued_rebalance is not None:
                cycle_intent = continued_rebalance

        if snapshot is not None:
            sweep_plan = self.plan_profit_sweep(
                snapshot=snapshot,
                bucket=self._profit_bucket,
                current_drawdown=current_drawdown,
            )
            self._log_profit_sweep_plan(
                plan=sweep_plan,
                snapshot=snapshot,
            )
            self._execute_profit_sweep(plan=sweep_plan, snapshot=snapshot)

        if cycle_intent is not None:
            return LiveCycleResult(
                selected_symbol=selector_snapshot.selected_symbol,
                intent=cycle_intent,
                dry_run=self._dry_run,
                risk_reason=risk_reason,
                risk_should_pause=risk_should_pause,
                risk_should_reduce=risk_should_reduce,
            )

        if selector_snapshot.selected_symbol is None:
            return LiveCycleResult(
                selected_symbol=None,
                intent=StrategyIntent.hold(),
                dry_run=self._dry_run,
                risk_reason=risk_reason,
                risk_should_pause=risk_should_pause,
                risk_should_reduce=risk_should_reduce,
            )

        previous_symbol = self._state.symbol or self._engine.symbol
        intent = self._engine.on_symbol_selected(selector_snapshot.selected_symbol)
        if intent.action == "open_hedge":
            self._state = self._new_state(
                underlying_symbol=derive_underlying_symbol(selector_snapshot.selected_symbol),
                symbol=selector_snapshot.selected_symbol,
                long_symbol=selector_snapshot.selected_symbol,
                short_symbol=selector_snapshot.selected_symbol,
                phase=self._engine.phase,
                long_notional=intent.long_notional,
                short_notional=intent.short_notional,
                long_filled=False,
                short_filled=False,
                last_symbol_switch_minute=self._resolve_last_symbol_switch_minute(
                    current_symbol=previous_symbol,
                    selected_symbol=selector_snapshot.selected_symbol,
                    minute_of_day=minute_of_day,
                ),
            )
            self._persist_state()
            self._execute_open_hedge(intent=intent, rows=eligible_rows)

        return LiveCycleResult(
            selected_symbol=selector_snapshot.selected_symbol,
            intent=intent,
            dry_run=self._dry_run,
            risk_reason=risk_reason,
            risk_should_pause=risk_should_pause,
            risk_should_reduce=risk_should_reduce,
        )

    def on_order_filled(self, symbol: str, position_side: str) -> None:
        tracked_symbols = {
            self._state.symbol,
            self._state.long_symbol,
            self._state.short_symbol,
        }
        if symbol not in tracked_symbols:
            return
        had_pending_rebalance = self._pending_rebalance_side is not None

        if position_side == "LONG":
            self._state.long_filled = True
            self._state.long_symbol = symbol
        elif position_side == "SHORT":
            self._state.short_filled = True
            self._state.short_symbol = symbol
        self._clear_failed_leg(symbol=symbol, position_side=position_side)

        if self._state.long_filled and self._state.short_filled:
            self._engine.mark_hedged(self._state.symbol or symbol)
            self._state.phase = StrategyPhase.HEDGED
            self._pending_rebalance_side = None
            if had_pending_rebalance:
                self._set_closed_loop_status(
                    action="restore_now",
                    closed_loop_ready=True,
                    sweep_block_reason=None,
                )
        else:
            self._state.phase = self._engine.phase

        self._persist_state()

    def handle_user_stream_event(self, payload: dict) -> None:
        event = self._stream_client.parse_user_stream_event(payload)
        if event is None:
            return None
        if event.get("event_type") == "order_filled":
            self.on_order_filled(
                symbol=event["symbol"],
                position_side=event["position_side"],
            )
            return None
        if event.get("event_type") == "order_failed":
            return self.on_order_failed(
                symbol=event["symbol"],
                position_side=event["position_side"],
                status=event["status"],
            )
        return None

    def consume_user_stream(
        self,
        event_source: Callable[[str], Iterable[dict]],
        keepalive_every: int = 50,
        max_events: int | None = None,
        retry_attempts: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ) -> int:
        return self._stream_client.run_user_stream_loop(
            event_source=event_source,
            handler=self.handle_user_stream_event,
            keepalive_every=keepalive_every,
            max_events=max_events,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
        )

    def on_order_failed(
        self,
        symbol: str,
        position_side: str,
        status: str,
    ) -> dict | None:
        tracked_symbols = {
            self._state.symbol,
            self._state.long_symbol,
            self._state.short_symbol,
        }
        if symbol not in tracked_symbols:
            return None

        key = (symbol, position_side)
        failure_count = self._failed_legs.get(key, 0) + 1
        self._failed_legs[key] = failure_count

        if failure_count == 1:
            action = {
                "action": "recover_missing_leg",
                "symbol": symbol,
                "position_side": position_side,
                "status": status,
            }
            self._execute_recovery_action(action)
            return action

        filled_side = "LONG" if self._state.long_filled else "SHORT" if self._state.short_filled else None
        if filled_side is None:
            return {
                "action": "pause_strategy",
                "symbol": symbol,
                "position_side": position_side,
                "status": status,
            }

        action = {
            "action": "flatten_exposure",
            "symbol": symbol,
            "position_side": filled_side,
            "status": status,
        }
        self._execute_recovery_action(action)
        return action

    def plan_profit_sweep(
        self,
        snapshot: PortfolioSnapshot,
        bucket: ProfitBucket,
        current_drawdown: Decimal = Decimal("0"),
    ) -> SweepPlan:
        if self._pending_rebalance_side is not None:
            return SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="pending_rebalance",
            )
        if self._engine.phase != StrategyPhase.HEDGED:
            return SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="not_hedged",
            )
        risk_decision = self._risk_manager.evaluate(
            snapshot=snapshot,
            current_drawdown=current_drawdown,
        )
        if risk_decision.should_pause or risk_decision.should_reduce:
            return SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="risk_block",
            )
        return self._transfer_planner.plan_sweep(snapshot=snapshot, bucket=bucket)

    def plan_redeem_topup(
        self,
        snapshot: PortfolioSnapshot,
    ) -> RedeemPlan:
        return self._transfer_planner.plan_redeem(
            snapshot=snapshot,
            bucket=self._profit_bucket,
        )

    def _execute_open_hedge(
        self,
        intent: StrategyIntent,
        rows: list[BacktestRow],
    ) -> None:
        if intent.symbol is None:
            return

        price = next(
            row["close"]
            for row in rows
            if row["symbol"] == intent.symbol
        )
        execution_route = self._resolve_execution_route(
            execution_stage="open_hedge",
            anchor_symbol=intent.symbol,
            reference_price=price,
        )
        if execution_route is None:
            return
        self._state.long_symbol = execution_route.symbol
        self._state.short_symbol = execution_route.symbol
        self._persist_state()
        if self._dry_run:
            self.run_dry_run_opening_tick(price)
            self._advance_dry_run_orders(price)
            return

        sizing_rule = self._account_service.get_symbol_order_sizing_rule(execution_route.symbol)
        quantity = normalize_order_quantity(
            target_notional=intent.long_notional,
            price=price,
            rule=sizing_rule,
        )
        if quantity is None:
            return

        self._account_service.set_um_position_mode(dual_side_position=True)
        order_kwargs = {}
        if execution_route.price is not None:
            order_kwargs["price"] = self._format_decimal(execution_route.price)
        if execution_route.time_in_force is not None:
            order_kwargs["time_in_force"] = execution_route.time_in_force
        if execution_route.reduce_only is not None:
            order_kwargs["reduce_only"] = execution_route.reduce_only

        self._account_service.place_um_order(
            symbol=execution_route.symbol,
            side="BUY",
            position_side="LONG",
            order_type=execution_route.order_type,
            quantity=str(quantity),
            **order_kwargs,
        )
        self._account_service.place_um_order(
            symbol=execution_route.symbol,
            side="SELL",
            position_side="SHORT",
            order_type=execution_route.order_type,
            quantity=str(quantity),
            **order_kwargs,
        )

    def _execute_recovery_action(self, action: dict) -> None:
        if self._dry_run:
            return

        symbol = action["symbol"]
        position_side = action["position_side"]

        if action["action"] == "recover_missing_leg":
            execution_route = self._resolve_execution_route(
                execution_stage="recover_missing_leg",
                anchor_symbol=self._state.symbol or symbol,
                reference_price=self._reference_price_for_symbol(symbol),
            )
            if execution_route is None:
                return
            quantity = self._normalized_quantity(execution_route.symbol)
            if quantity is None:
                return

            if position_side == "LONG":
                self._state.long_symbol = execution_route.symbol
            else:
                self._state.short_symbol = execution_route.symbol
            self._persist_state()

            side = "BUY" if position_side == "LONG" else "SELL"
            order_kwargs = {}
            if execution_route.price is not None:
                order_kwargs["price"] = self._format_decimal(execution_route.price)
            if execution_route.time_in_force is not None:
                order_kwargs["time_in_force"] = execution_route.time_in_force
            if execution_route.reduce_only is not None:
                order_kwargs["reduce_only"] = execution_route.reduce_only
            self._account_service.place_um_order(
                symbol=execution_route.symbol,
                side=side,
                position_side=position_side,
                order_type=execution_route.order_type,
                quantity=str(quantity),
                **order_kwargs,
            )
            return

        quantity = self._normalized_quantity(symbol)
        if quantity is None:
            return

        if action["action"] == "flatten_exposure":
            self._account_service.close_position(
                symbol=symbol,
                position_side=position_side,
                quantity=str(quantity),
            )

    def _clear_failed_leg(self, symbol: str, position_side: str) -> None:
        self._failed_legs.pop((symbol, position_side), None)

    def _normalized_quantity(self, symbol: str) -> Decimal | None:
        price = self._latest_prices.get(symbol)
        if price is None:
            price = self._reference_price_for_symbol(symbol)
        if price is None:
            return None

        sizing_rule = self._account_service.get_symbol_order_sizing_rule(symbol)
        return normalize_order_quantity(
            target_notional=self._state.long_notional,
            price=price,
            rule=sizing_rule,
        )

    def _execute_profit_sweep(
        self,
        plan: SweepPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        if not plan.should_subscribe_rwusd or plan.usdt_amount <= Decimal("0"):
            return

        if not self._dry_run:
            amount = str(plan.usdt_amount)
            self._account_service.transfer_pm_to_spot(asset="USDT", amount=amount)
            self._account_service.subscribe_rwusd(amount=amount)
        self._profit_bucket = self._pnl_manager.record_sweep(
            bucket=self._profit_bucket,
            sweep_amount=plan.usdt_amount,
        )
        self._persist_state()
        self._log_profit_sweep_executed(
            plan=plan,
            snapshot=snapshot,
        )

    def _execute_redeem_topup(
        self,
        plan: RedeemPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        if not plan.should_redeem_rwusd or plan.usdt_amount <= Decimal("0"):
            return

        if not self._dry_run:
            amount = str(plan.usdt_amount)
            self._account_service.redeem_rwusd(amount=amount)
            self._account_service.transfer_spot_to_pm(asset="USDT", amount=amount)
        self._profit_bucket = ProfitBucket(
            realized_pnl_total=self._profit_bucket.realized_pnl_total,
            realized_pnl_available_for_deposit=self._profit_bucket.realized_pnl_available_for_deposit,
            rwusd_principal=max(Decimal("0"), self._profit_bucket.rwusd_principal - plan.usdt_amount),
            rwusd_interest_accrued=self._profit_bucket.rwusd_interest_accrued,
            rwusd_redeemable=max(Decimal("0"), self._profit_bucket.rwusd_redeemable - plan.usdt_amount),
            harvest_count=self._profit_bucket.harvest_count,
            deposit_count=self._profit_bucket.deposit_count,
            redeem_count=self._profit_bucket.redeem_count + 1,
        )
        self._persist_state()
        self._log_redeem_topup_executed(
            plan=plan,
            snapshot=snapshot,
        )

    def _reset_state(self) -> None:
        self._state = self._new_state()
        self._engine.symbol = None
        self._engine.phase = StrategyPhase.IDLE
        self._pending_rebalance_side = None
        self._failed_legs.clear()
        self._persist_state()

    def _restore_remote_hedge(self, remote_position) -> None:
        self._state = self._new_state(
            underlying_symbol=remote_position.underlying_symbol,
            symbol=remote_position.anchor_symbol,
            long_symbol=remote_position.long_symbol,
            short_symbol=remote_position.short_symbol,
            phase=StrategyPhase.HEDGED,
            long_notional=remote_position.long_notional,
            short_notional=remote_position.short_notional,
            long_filled=True,
            short_filled=True,
        )
        self._engine.mark_hedged(remote_position.anchor_symbol)
        self._pending_rebalance_side = None
        self._failed_legs.clear()
        self._persist_state()

    def _maybe_handle_take_profit(
        self,
        rows: list[BacktestRow],
        selected_symbol: str,
        snapshot: PortfolioSnapshot | None,
        risk_should_pause: bool,
        risk_should_reduce: bool,
        allow_restore_now: bool,
    ) -> StrategyIntent | None:
        if self._engine.phase != StrategyPhase.HEDGED:
            return None
        if self._engine.symbol != selected_symbol:
            return None

        row = next(
            (item for item in rows if item["symbol"] == selected_symbol),
            None,
        )
        if row is None:
            return None

        long_unrealized = row.get("long_unrealized")
        short_unrealized = row.get("short_unrealized")
        if long_unrealized is None or short_unrealized is None:
            return None

        take_profit_intent = self._engine.on_pnl_update(
            long_unrealized=long_unrealized,
            short_unrealized=short_unrealized,
        )
        if take_profit_intent.action != "take_profit":
            return None

        harvest_decision = self._harvest_rule.evaluate(
            symbol=selected_symbol,
            side=take_profit_intent.side,
            target_notional=self._target_notional_for_side(take_profit_intent.side),
            unrealized_pnl=take_profit_intent.unrealized_pnl,
            recent_funding_cost=Decimal(str(row.get("recent_funding_cost", Decimal("0")))),
            uni_mmr=snapshot.uni_mmr if snapshot is not None else Decimal("999999"),
            min_safe_unimmr=getattr(self._risk_manager, "_soft_unimmr", Decimal("0")),
        )
        if not harvest_decision.should_harvest:
            self._engine.mark_hedged(selected_symbol)
            self._state.phase = StrategyPhase.HEDGED
            self._state.long_filled = True
            self._state.short_filled = True
            self._pending_rebalance_side = None
            self._persist_state()
            return StrategyIntent.hold()

        close_executed = self._execute_take_profit_close(take_profit_intent)
        if not close_executed:
            self._engine.mark_hedged(selected_symbol)
            self._state.phase = StrategyPhase.HEDGED
            self._state.long_filled = True
            self._state.short_filled = True
            self._pending_rebalance_side = None
            self._persist_state()
            return StrategyIntent.hold()

        if take_profit_intent.side == PositionSide.LONG:
            self._state.long_filled = False
        elif take_profit_intent.side == PositionSide.SHORT:
            self._state.short_filled = False
        self._pending_rebalance_side = take_profit_intent.side
        self._profit_bucket = self._pnl_manager.record_take_profit(
            bucket=self._profit_bucket,
            realized_pnl=harvest_decision.net_pnl,
        )
        rebalance_intent = self._engine.on_take_profit_completed(
            symbol=selected_symbol,
            closed_side=take_profit_intent.side,
        )
        self._state.phase = self._engine.phase
        self._persist_state()

        rebalance_decision = self._rebalance_planner.decide(
            current_phase=self._engine.phase,
            risk_should_reduce=risk_should_reduce,
            risk_should_pause=risk_should_pause,
            bull_mode=self._bull_mode,
            allow_restore_now=allow_restore_now,
        )
        self._log_rebalance_decision(
            symbol=selected_symbol,
            action=rebalance_decision.action,
            closed_side=take_profit_intent.side.value if take_profit_intent.side is not None else None,
            realized_pnl=harvest_decision.net_pnl,
        )
        if rebalance_decision.action == "restore_now":
            self._execute_restore_now(rebalance_intent)
            self._set_closed_loop_status(
                action="restore_now",
                closed_loop_ready=(
                    self._engine.phase == StrategyPhase.HEDGED
                    and self._pending_rebalance_side is None
                ),
                sweep_block_reason=(
                    None
                    if self._engine.phase == StrategyPhase.HEDGED
                    and self._pending_rebalance_side is None
                    else "pending_rebalance"
                ),
            )
            return rebalance_intent
        if rebalance_decision.action == "reduce_risk":
            self._execute_reduce_risk(rebalance_intent)
            self._set_closed_loop_status(
                action="reduce_risk",
                closed_loop_ready=False,
                sweep_block_reason="risk_block",
            )
            return StrategyIntent.hold()
        self._set_closed_loop_status(
            action="restore_later",
            closed_loop_ready=False,
            sweep_block_reason="pending_rebalance",
        )
        return StrategyIntent.hold()

    def _execute_take_profit_close(self, intent: StrategyIntent) -> bool:
        if intent.action != "take_profit" or intent.symbol is None or intent.side is None:
            return False
        if self._dry_run:
            if intent.side == PositionSide.LONG:
                if self._state.sim_long_qty <= Decimal("0"):
                    return False
                self._state.sim_long_qty = Decimal("0")
                self._state.sim_long_unrealized_pnl = Decimal("0")
            else:
                if self._state.sim_short_qty <= Decimal("0"):
                    return False
                self._state.sim_short_qty = Decimal("0")
                self._state.sim_short_unrealized_pnl = Decimal("0")
            self._state.sim_take_profit_count += 1
            self._persist_state()
            return True

        close_symbol = self._tracked_symbol_for_side(intent.side)
        if close_symbol is None:
            return False

        quantity = self._normalized_quantity(close_symbol)
        if quantity is None:
            return False

        self._account_service.close_position(
            symbol=close_symbol,
            position_side=intent.side.value,
            quantity=str(quantity),
        )
        return True

    def _maybe_continue_rebalancing(
        self,
        selected_symbol: str,
        risk_should_pause: bool,
        risk_should_reduce: bool,
        allow_restore_now: bool,
    ) -> StrategyIntent | None:
        if self._engine.phase != StrategyPhase.REBALANCING:
            return None
        if self._engine.symbol != selected_symbol:
            return None
        if self._pending_rebalance_side is None:
            return None

        rebalance_decision = self._rebalance_planner.decide(
            current_phase=self._engine.phase,
            risk_should_reduce=risk_should_reduce,
            risk_should_pause=risk_should_pause,
            bull_mode=self._bull_mode,
            allow_restore_now=allow_restore_now,
        )
        rebalance_intent = StrategyIntent.rebalance(
            symbol=selected_symbol,
            closed_side=self._pending_rebalance_side,
        )
        self._log_rebalance_decision(
            symbol=selected_symbol,
            action=rebalance_decision.action,
            closed_side=self._pending_rebalance_side.value,
            realized_pnl=Decimal("0"),
        )
        if rebalance_decision.action == "restore_now":
            self._execute_restore_now(rebalance_intent)
            self._set_closed_loop_status(
                action="restore_now",
                closed_loop_ready=(
                    self._engine.phase == StrategyPhase.HEDGED
                    and self._pending_rebalance_side is None
                ),
                sweep_block_reason=(
                    None
                    if self._engine.phase == StrategyPhase.HEDGED
                    and self._pending_rebalance_side is None
                    else "pending_rebalance"
                ),
            )
            return rebalance_intent
        if rebalance_decision.action == "reduce_risk":
            self._execute_reduce_risk(rebalance_intent)
            self._set_closed_loop_status(
                action="reduce_risk",
                closed_loop_ready=False,
                sweep_block_reason="risk_block",
            )
            return StrategyIntent.hold()
        self._set_closed_loop_status(
            action="restore_later",
            closed_loop_ready=False,
            sweep_block_reason="pending_rebalance",
        )
        return StrategyIntent.hold()

    def _execute_restore_now(self, intent: StrategyIntent) -> None:
        if intent.action != "rebalance" or intent.symbol is None or intent.side is None:
            return

        current_symbol = self._tracked_symbol_for_side(intent.side)
        if current_symbol is None:
            return
        restore_route = self._resolve_execution_route(
            execution_stage="restore_now",
            anchor_symbol=self._state.symbol or intent.symbol,
            reference_price=self._reference_price_for_symbol(current_symbol),
        )
        if restore_route is None:
            return
        if self._dry_run:
            restore_price = self._state.sim_last_mark_price
            if restore_price <= Decimal("0"):
                reference_price = self._reference_price_for_symbol(restore_route.symbol)
                if reference_price is None or reference_price <= Decimal("0"):
                    return
                restore_price = reference_price

            quantity = self._simulate_quantity(
                price=restore_price,
                notional=self._target_notional_for_side(intent.side),
            )
            if quantity <= Decimal("0"):
                return

            if intent.side == PositionSide.LONG:
                self._state.long_symbol = restore_route.symbol
                self._state.sim_long_qty = quantity
                self._state.sim_long_entry_price = restore_price
                self._state.sim_long_unrealized_pnl = Decimal("0")
                self._state.long_filled = True
            else:
                self._state.short_symbol = restore_route.symbol
                self._state.sim_short_qty = quantity
                self._state.sim_short_entry_price = restore_price
                self._state.sim_short_unrealized_pnl = Decimal("0")
                self._state.short_filled = True

            self._state.sim_restore_count += 1
            self._pending_rebalance_side = None
            self._engine.on_rebalance_restored(intent.symbol)
            self._state.phase = self._engine.phase
            self._persist_state()
            return

        quantity = self._normalized_quantity(restore_route.symbol)
        if quantity is None:
            return

        if intent.side == PositionSide.LONG:
            self._state.long_symbol = restore_route.symbol
        else:
            self._state.short_symbol = restore_route.symbol

        restore_side = "BUY" if intent.side.value == "LONG" else "SELL"
        if restore_route.order_type == "LIMIT" and restore_route.price is not None:
            self._account_service.place_um_order(
                symbol=restore_route.symbol,
                side=restore_side,
                position_side=intent.side.value,
                order_type=restore_route.order_type,
                quantity=str(quantity),
                price=self._format_decimal(restore_route.price),
                time_in_force=restore_route.time_in_force,
                reduce_only=restore_route.reduce_only,
            )
            return
        self._account_service.place_um_order(
            symbol=restore_route.symbol,
            side=restore_side,
            position_side=intent.side.value,
            order_type=restore_route.order_type,
            quantity=str(quantity),
        )

    def _execute_reduce_risk(self, intent: StrategyIntent) -> None:
        if intent.action != "rebalance" or intent.symbol is None or intent.side is None:
            return

        remaining_side = "SHORT" if intent.side.value == "LONG" else "LONG"
        action = {
            "action": "flatten_exposure",
            "symbol": intent.symbol,
            "position_side": remaining_side,
        }
        self._execute_recovery_action(action)
        self._pending_rebalance_side = None
        self._engine.phase = StrategyPhase.PAUSED
        self._state.phase = StrategyPhase.PAUSED
        self._persist_state()

    def _set_closed_loop_status(
        self,
        action: str,
        closed_loop_ready: bool,
        sweep_block_reason: str | None,
    ) -> None:
        self._profit_bucket = ProfitBucket(
            realized_pnl_total=self._profit_bucket.realized_pnl_total,
            realized_pnl_available_for_deposit=self._profit_bucket.realized_pnl_available_for_deposit,
            harvest_buffer=self._profit_bucket.harvest_buffer,
            rwusd_principal=self._profit_bucket.rwusd_principal,
            rwusd_interest_accrued=self._profit_bucket.rwusd_interest_accrued,
            rwusd_redeemable=self._profit_bucket.rwusd_redeemable,
            harvest_count=self._profit_bucket.harvest_count,
            deposit_count=self._profit_bucket.deposit_count,
            redeem_count=self._profit_bucket.redeem_count,
            closed_loop_ready=closed_loop_ready,
            last_rebalance_action=action,
            sweep_block_reason=sweep_block_reason,
        )

    def _select_symbol_snapshot(
        self,
        rows: list[BacktestRow],
        minute_of_day: int | None,
    ):
        select_signature = signature(self._selector.select)
        if (
            "minute_of_day" in select_signature.parameters
            and "last_switch_minute" in select_signature.parameters
        ):
            selector_snapshot = self._selector.select(
                current_symbol=self._engine.symbol,
                rows=rows,
                minute_of_day=minute_of_day,
                last_switch_minute=self._state.last_symbol_switch_minute,
            )
        elif "minute_of_day" in select_signature.parameters:
            selector_snapshot = self._selector.select(
                current_symbol=self._engine.symbol,
                rows=rows,
                minute_of_day=minute_of_day,
            )
        else:
            selector_snapshot = self._selector.select(
                current_symbol=self._engine.symbol,
                rows=rows,
            )
        return self._hold_current_symbol_during_active_cycle(selector_snapshot)

    def _hold_current_symbol_during_active_cycle(self, selector_snapshot):
        if self._engine.symbol is None:
            return selector_snapshot
        should_hold = self._engine.phase in (
            StrategyPhase.OPENING_HEDGE,
            StrategyPhase.TAKING_PROFIT,
            StrategyPhase.HEDGED,
        ) or (
            self._engine.phase == StrategyPhase.REBALANCING
            and self._pending_rebalance_side is not None
        )
        if not should_hold:
            return selector_snapshot
        selector_snapshot.selected_symbol = self._engine.symbol
        selector_snapshot.selected_symbols = [self._engine.symbol]
        return selector_snapshot

    def _is_eval_checkpoint(self, minute_of_day: int | None) -> bool:
        if minute_of_day is None:
            return True
        return minute_of_day % self._selector_eval_interval_minutes == 0

    def _target_notional_for_side(self, side: PositionSide | None) -> Decimal:
        if side == PositionSide.SHORT and self._state.short_notional > Decimal("0"):
            return self._state.short_notional
        if self._state.long_notional > Decimal("0"):
            return self._state.long_notional
        if self._state.short_notional > Decimal("0"):
            return self._state.short_notional
        return Decimal("0")

    def _tracked_symbol_for_side(self, side: PositionSide) -> str | None:
        if side == PositionSide.LONG:
            return self._state.long_symbol or self._state.symbol
        return self._state.short_symbol or self._state.symbol

    def _reference_price_for_symbol(self, symbol: str) -> Decimal | None:
        direct = self._latest_prices.get(symbol)
        if direct is not None:
            return direct
        if self._state.symbol is not None:
            anchor = self._latest_prices.get(self._state.symbol)
            if anchor is not None:
                return anchor
        target_underlying = derive_underlying_symbol(symbol)
        for candidate_symbol, price in self._latest_prices.items():
            if derive_underlying_symbol(candidate_symbol) == target_underlying:
                return price
        return None

    def _simulate_quantity(self, price: Decimal, notional: Decimal) -> Decimal:
        if price <= Decimal("0") or notional <= Decimal("0"):
            return Decimal("0")
        return notional / price

    def _mark_virtual_positions_from_rows(self, rows: list[BacktestRow]) -> None:
        if not self._dry_run or self._state.symbol is None:
            return
        if self._engine.phase != StrategyPhase.HEDGED:
            return
        if self._state.sim_long_qty <= Decimal("0") and self._state.sim_short_qty <= Decimal("0"):
            return

        row = next(
            (item for item in rows if item["symbol"] == self._state.symbol),
            None,
        )
        if row is None:
            return

        self._mark_virtual_positions(mark_price=row["close"])
        row.setdefault("long_unrealized", self._state.sim_long_unrealized_pnl)
        row.setdefault("short_unrealized", self._state.sim_short_unrealized_pnl)

    def _mark_virtual_positions(self, mark_price: Decimal) -> None:
        self._state.sim_last_mark_price = mark_price
        self._state.sim_long_unrealized_pnl = (
            mark_price - self._state.sim_long_entry_price
        ) * self._state.sim_long_qty
        self._state.sim_short_unrealized_pnl = (
            self._state.sim_short_entry_price - mark_price
        ) * self._state.sim_short_qty
        self._persist_state()

    def _load_runtime_state(self) -> dict[str, object] | None:
        loader = getattr(self._state_store, "load_runtime_state", None)
        if not callable(loader):
            return None
        loaded = loader()
        return loaded if isinstance(loaded, dict) else None

    def _restore_dry_run_order_lifecycle(self) -> None:
        if not self._dry_run:
            return
        loader = getattr(self._state_store, "load_dry_run_order_lifecycle", None)
        if not callable(loader):
            return
        snapshot = loader()
        if not isinstance(snapshot, dict):
            return
        self._dry_run_order_lifecycle = DryRunOrderLifecycle.from_snapshot(snapshot)

    def _dry_run_quote_runtime(self, mid_price: Decimal) -> DryRunQuoteRuntime:
        if mid_price <= Decimal("0"):
            raise ValueError("mid_price_must_be_positive")
        target_notional = max(
            self._target_notional,
            self._state.long_notional,
            self._state.short_notional,
        )
        return DryRunQuoteRuntime(
            TickQuotePlanner(target_quantity=target_notional / mid_price),
            self._require_dry_run_order_lifecycle(),
        )

    def _require_dry_run_order_lifecycle(self) -> DryRunOrderLifecycle:
        if not self._dry_run or self._dry_run_order_lifecycle is None:
            raise RuntimeError("dry_run_order_lifecycle_requires_dry_run")
        return self._dry_run_order_lifecycle

    def _sync_dry_run_lifecycle_state(self, mark_price: Decimal) -> None:
        lifecycle = self._require_dry_run_order_lifecycle()
        self._state.sim_long_qty = lifecycle.long_quantity
        self._state.sim_short_qty = lifecycle.short_quantity
        self._state.sim_last_mark_price = mark_price
        self._persist_state()

    def _seed_dry_run_lifecycle_from_state(self) -> None:
        lifecycle = self._require_dry_run_order_lifecycle()
        if (
            lifecycle.long_quantity != Decimal("0")
            or lifecycle.short_quantity != Decimal("0")
            or self._state.sim_long_qty == Decimal("0")
            and self._state.sim_short_qty == Decimal("0")
        ):
            return
        self._dry_run_order_lifecycle = DryRunOrderLifecycle(
            long_quantity=self._state.sim_long_qty,
            short_quantity=self._state.sim_short_qty,
        )

    def _advance_dry_run_orders(
        self,
        mark_price: Decimal,
        *,
        advance_cycle: bool = False,
    ) -> None:
        if not self._dry_run or self._dry_run_matching_engine is None:
            return
        self._seed_dry_run_lifecycle_from_state()
        lifecycle = self._require_dry_run_order_lifecycle()
        matched_orders = self._dry_run_matching_engine.match(lifecycle, mark_price)
        expired_orders = ()
        if advance_cycle:
            lifecycle.advance_cycle()
            expired_orders = lifecycle.expire_orders_older_than(
                self._dry_run_order_timeout_cycles
            )
            if expired_orders:
                requote_count = lifecycle.register_timeout_requote()
                if (
                    self._dry_run_max_requotes > 0
                    and requote_count > self._dry_run_max_requotes
                ):
                    self._engine.phase = StrategyPhase.PAUSED
                    self._state.phase = StrategyPhase.PAUSED
                    self._sync_dry_run_lifecycle_state(mark_price)
                    return
                self.run_dry_run_opening_tick(mark_price)
        if not matched_orders:
            if expired_orders:
                self._sync_dry_run_lifecycle_state(mark_price)
            return
        for order in matched_orders:
            if order.reduce_only:
                continue
            if order.position_side == PositionSide.LONG:
                if self._state.sim_long_entry_price <= Decimal("0"):
                    self._state.sim_long_entry_price = mark_price
            elif self._state.sim_short_entry_price <= Decimal("0"):
                self._state.sim_short_entry_price = mark_price
        target_quantity = self._target_notional_for_side(None) / mark_price
        if (
            self._engine.phase == StrategyPhase.OPENING_HEDGE
            and lifecycle.long_quantity >= target_quantity
            and lifecycle.short_quantity >= target_quantity
        ):
            self._state.long_filled = True
            self._state.short_filled = True
            self._engine.mark_hedged(self._state.symbol or self._engine.symbol or "")
            self._state.phase = self._engine.phase
            self._pending_rebalance_side = None
            lifecycle.reset_timeout_requotes()
        self._state.sim_long_unrealized_pnl = Decimal("0")
        self._state.sim_short_unrealized_pnl = Decimal("0")
        self._sync_dry_run_lifecycle_state(mark_price)

    def _persist_state(self) -> None:
        self._state_store.save_hedge_state(self._state)
        saver = getattr(self._state_store, "save_runtime_state", None)
        if not callable(saver):
            return
        saver(
            profit_bucket=self._profit_bucket,
            pending_rebalance_side=self._pending_rebalance_side,
        )
        lifecycle_saver = getattr(
            self._state_store,
            "save_dry_run_order_lifecycle",
            None,
        )
        if callable(lifecycle_saver) and self._dry_run_order_lifecycle is not None:
            lifecycle_saver(self._dry_run_order_lifecycle.to_snapshot())

    def _format_decimal(self, value: Decimal) -> str:
        rendered = format(value, "f")
        if "." not in rendered:
            return rendered
        return rendered.rstrip("0").rstrip(".")

    def _resolve_execution_route(
        self,
        execution_stage: str,
        anchor_symbol: str,
        reference_price: Decimal | None,
    ):
        route = build_execution_route(
            execution_stage=execution_stage,
            anchor_symbol=anchor_symbol,
            available_symbols=self._route_symbols,
            reference_price=reference_price,
            maker_enabled=self._usdc_maker_enabled,
            maker_allowed_phases=self._usdc_maker_allowed_phases,
            fallback_to_market_on_missing_price=(
                self._usdc_maker_fallback_to_market_on_missing_price
            ),
        )
        self._log_execution_route(route)
        return route

    def _new_state(self, **kwargs) -> HedgeState:
        return HedgeState(
            sim_leverage=self._sim_leverage,
            **kwargs,
        )

    def _resolve_last_symbol_switch_minute(
        self,
        current_symbol: str | None,
        selected_symbol: str | None,
        minute_of_day: int | None,
    ) -> int | None:
        if selected_symbol is None or selected_symbol == current_symbol or minute_of_day is None:
            return self._state.last_symbol_switch_minute
        return minute_of_day

    def _log_selector_snapshot(self, snapshot) -> None:
        self._logger.log(
            level="INFO",
            message="selector snapshot evaluated",
            event="live.selector_snapshot",
            context={
                "current_symbol": self._engine.symbol,
                "selected_symbol": snapshot.selected_symbol,
                "scores": [
                    {
                        "symbol": item.symbol,
                        "score": str(item.score),
                        "liquidity": str(item.liquidity_score),
                        "volatility": str(item.volatility_score),
                        "funding": str(item.funding_score),
                        "margin": str(item.margin_efficiency_score),
                        "execution_cost_score": str(item.execution_cost_score),
                        "execution_cost_bps": str(item.execution_cost_bps),
                        "preferred_execution_symbol": item.preferred_execution_symbol,
                        "reject_reason": item.reject_reason,
                    }
                    for item in snapshot.scores
                ],
            },
        )

    def _enrich_selector_rows(self, rows: list[BacktestRow]) -> list[BacktestRow]:
        enriched_rows: list[BacktestRow] = []
        for row in rows:
            preference = build_execution_preference(
                anchor_symbol=row["symbol"],
                available_symbols=self._route_symbols,
            )
            enriched_rows.append(
                {
                    **row,
                    "execution_cost_bps": preference.execution_cost_bps,
                    "preferred_execution_symbol": preference.preferred_symbol,
                }
            )
        return enriched_rows

    def _log_risk_decision(
        self,
        reason: str | None,
        should_pause: bool,
        should_reduce: bool,
        current_drawdown: Decimal,
        snapshot: PortfolioSnapshot,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="risk decision evaluated",
            event="live.risk_decision",
            context={
                "reason": reason,
                "should_pause": should_pause,
                "should_reduce": should_reduce,
                "current_drawdown": str(current_drawdown),
                "uni_mmr": str(snapshot.uni_mmr),
                "account_equity": str(snapshot.account_equity),
            },
        )

    def _log_execution_route(self, route) -> None:
        self._logger.log(
            level="INFO",
            message="execution route resolved",
            event="live.execution_route",
            context={
                "anchor_symbol": route.anchor_symbol,
                "execution_symbol": route.symbol,
                "execution_stage": route.execution_stage,
                "maker_only": route.maker_only,
                "fallback_reason": route.fallback_reason,
            },
        )

    def _log_rebalance_decision(
        self,
        symbol: str,
        action: str,
        closed_side: str | None,
        realized_pnl: Decimal,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="rebalance decision evaluated",
            event="live.rebalance_decision",
            context={
                "symbol": symbol,
                "action": action,
                "closed_side": closed_side,
                "realized_pnl": str(realized_pnl),
            },
        )

    def _log_profit_sweep_plan(
        self,
        plan: SweepPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="profit sweep plan evaluated",
            event="live.profit_sweep_plan",
            context={
                "usdt_amount": str(plan.usdt_amount),
                "should_subscribe_rwusd": plan.should_subscribe_rwusd,
                "available_balance": str(snapshot.available_balance),
                "uni_mmr": str(snapshot.uni_mmr),
            },
        )

    def _log_redeem_topup_plan(
        self,
        plan: RedeemPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="redeem topup plan evaluated",
            event="live.redeem_topup_plan",
            context={
                "usdt_amount": str(plan.usdt_amount),
                "should_redeem_rwusd": plan.should_redeem_rwusd,
                "available_balance": str(snapshot.available_balance),
                "uni_mmr": str(snapshot.uni_mmr),
            },
        )

    def _log_profit_sweep_executed(
        self,
        plan: SweepPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="profit sweep executed",
            event="live.profit_sweep_executed",
            context={
                "usdt_amount": str(plan.usdt_amount),
                "available_balance": str(snapshot.available_balance),
                "uni_mmr": str(snapshot.uni_mmr),
            },
        )

    def _log_redeem_topup_executed(
        self,
        plan: RedeemPlan,
        snapshot: PortfolioSnapshot,
    ) -> None:
        self._logger.log(
            level="INFO",
            message="redeem topup executed",
            event="live.redeem_topup_executed",
            context={
                "usdt_amount": str(plan.usdt_amount),
                "available_balance": str(snapshot.available_balance),
                "uni_mmr": str(snapshot.uni_mmr),
            },
        )
