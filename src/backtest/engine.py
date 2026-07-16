from dataclasses import dataclass, field, replace
from decimal import Decimal

from src.backtest.datasource import BacktestRow
from src.domain.enums import PositionSide, StrategyPhase
from src.domain.models import PortfolioSnapshot, ProfitBucket
from src.market.selector import SymbolSelector
from src.portfolio.yield_accrual import accrue_rwusd_interest
from src.risk.rules import RiskDecision, RiskRuleSet
from src.strategy.hedge_engine import HedgeEngine
from src.strategy.harvest_rules import HarvestRule
from src.strategy.pnl_manager import PnlManager
from src.strategy.rebalance import RebalancePlanner
from src.portfolio.transfers import TransferPlanner


ZERO = Decimal("0")


@dataclass(slots=True)
class BacktestRuntimeState:
    current_symbol: str | None = None
    phase: StrategyPhase = StrategyPhase.IDLE
    pending_rebalance_side: PositionSide | None = None
    last_symbol_switch_minute: int | None = None
    profit_bucket: ProfitBucket = field(default_factory=ProfitBucket)
    sim_long_qty: Decimal = ZERO
    sim_short_qty: Decimal = ZERO
    sim_long_entry_price: Decimal = ZERO
    sim_short_entry_price: Decimal = ZERO
    sim_long_unrealized_pnl: Decimal = ZERO
    sim_short_unrealized_pnl: Decimal = ZERO
    sim_last_mark_price: Decimal = ZERO
    sim_take_profit_count: int = 0
    sim_restore_count: int = 0


@dataclass(slots=True)
class BacktestResult:
    selected_symbols: list[str] = field(default_factory=list)
    realized_pnl: Decimal = ZERO
    take_profit_count: int = 0
    restore_now_count: int = 0
    restore_later_count: int = 0
    reduce_risk_count: int = 0
    profit_sweep_count: int = 0
    redeem_count: int = 0
    rebalance_action_counts: dict[str, int] = field(default_factory=dict)
    rwusd_principal: Decimal = ZERO
    rwusd_interest_accrued: Decimal = ZERO
    bucket: ProfitBucket = field(default_factory=ProfitBucket)
    current_symbol: str | None = None
    phase: StrategyPhase = StrategyPhase.IDLE
    pending_rebalance_side: PositionSide | None = None
    last_symbol_switch_minute: int | None = None
    runtime_state: BacktestRuntimeState | None = None


class BacktestEngine:
    def __init__(
        self,
        max_active_symbols: int = 1,
        selector_switch_cooldown_minutes: int = 60,
    ) -> None:
        self._target_notional = Decimal("1000")
        self._max_active_symbols = max(1, max_active_symbols)
        self._selector = SymbolSelector(
            switch_threshold=Decimal("0.20"),
            switch_cooldown_minutes=selector_switch_cooldown_minutes,
        )
        self._pnl_manager = PnlManager(
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
        )
        self._harvest_rule = HarvestRule(
            taker_fee_bps=Decimal("5"),
            slippage_bps=Decimal("5"),
            min_net_pnl=Decimal("18"),
        )
        self._rebalance_planner = RebalancePlanner()
        self._risk_manager = RiskRuleSet(
            soft_unimmr=Decimal("6"),
            hard_unimmr=Decimal("4"),
            max_drawdown=Decimal("0.15"),
            redeem_unimmr=Decimal("6"),
            reserve_available_balance=Decimal("100"),
        )
        self._transfer_planner = TransferPlanner(
            min_sweep=Decimal("50"),
            pm_reserve=Decimal("100"),
            min_redeem=Decimal("50"),
            redeem_unimmr=Decimal("6"),
        )
        self._rwusd_apr = Decimal("0.12")

    def run(
        self,
        rows: list[BacktestRow],
        snapshot: PortfolioSnapshot | dict | None = None,
        bucket: ProfitBucket | dict | None = None,
        current_drawdown: Decimal = ZERO,
        bull_mode: bool = False,
        elapsed_hours: Decimal = ZERO,
        current_symbol: str | None = None,
        current_phase: StrategyPhase = StrategyPhase.IDLE,
        pending_rebalance_side: PositionSide | None = None,
        last_switch_minute: int | None = None,
        allow_restore_now: bool | None = None,
        minute_of_day: int | None = None,
        runtime_state: BacktestRuntimeState | None = None,
    ) -> BacktestResult:
        runtime = self._resolve_runtime_state(
            runtime_state=runtime_state,
            bucket=bucket,
            current_symbol=current_symbol,
            current_phase=current_phase,
            pending_rebalance_side=pending_rebalance_side,
            last_switch_minute=last_switch_minute,
        )
        bucket = runtime.profit_bucket
        current_symbol = runtime.current_symbol
        current_phase = runtime.phase
        pending_rebalance_side = runtime.pending_rebalance_side
        last_switch_minute = runtime.last_symbol_switch_minute
        capital_snapshot = self._coerce_snapshot(snapshot)
        profit_bucket = self._coerce_bucket(bucket)
        if profit_bucket.rwusd_principal > ZERO and elapsed_hours > ZERO:
            profit_bucket = accrue_rwusd_interest(
                bucket=profit_bucket,
                apr=self._rwusd_apr,
                elapsed_hours=elapsed_hours,
            )
        runtime.profit_bucket = profit_bucket

        if self._max_active_symbols == 1:
            selector_snapshot = self._selector.select(
                current_symbol=current_symbol,
                rows=rows,
                minute_of_day=minute_of_day,
                last_switch_minute=last_switch_minute,
            )
        else:
            selector_snapshot = self._selector.select_many(
                rows=rows,
                limit=self._max_active_symbols,
            )
        selector_snapshot = self._hold_current_symbol_during_active_cycle(
            selector_snapshot=selector_snapshot,
            current_symbol=current_symbol,
            current_phase=current_phase,
            pending_rebalance_side=pending_rebalance_side,
        )
        resolved_allow_restore_now = self._resolve_allow_restore_now(
            allow_restore_now=allow_restore_now,
            minute_of_day=minute_of_day,
        )
        result = BacktestResult(bucket=profit_bucket)
        result.selected_symbols.extend(selector_snapshot.selected_symbols)
        result.last_symbol_switch_minute = self._resolve_last_switch_minute(
            current_symbol=current_symbol,
            selected_symbol=selector_snapshot.selected_symbol,
            last_switch_minute=last_switch_minute,
            minute_of_day=minute_of_day,
        )
        if not selector_snapshot.selected_symbols:
            result.current_symbol = current_symbol
            result.phase = current_phase
            result.pending_rebalance_side = pending_rebalance_side
            return self._finalize_result(result=result, bucket=profit_bucket)

        risk_decision = self._evaluate_risk(
            snapshot=capital_snapshot,
            current_drawdown=current_drawdown,
        )
        rows_by_symbol = {row["symbol"]: row for row in rows}
        for selected_symbol in selector_snapshot.selected_symbols:
            selected_row = rows_by_symbol[selected_symbol]
            selected_price = Decimal(str(selected_row.get("close", ZERO)))
            hedge_engine = self._new_hedge_engine()
            continuation_enabled = self._max_active_symbols == 1
            active_phase = current_phase if continuation_enabled else StrategyPhase.HEDGED
            active_pending_side = pending_rebalance_side if continuation_enabled else None
            self._seed_hedge_engine(
                hedge_engine=hedge_engine,
                selected_symbol=selected_symbol,
                current_symbol=current_symbol,
                current_phase=active_phase,
            )
            result.current_symbol = selected_symbol
            if selected_symbol != current_symbol:
                self._initialize_sim_hedge(
                    runtime=runtime,
                    selected_symbol=selected_symbol,
                    price=selected_price,
                )
            self._update_sim_mark(
                runtime=runtime,
                mark_price=selected_price,
                long_unrealized=selected_row.get("long_unrealized"),
                short_unrealized=selected_row.get("short_unrealized"),
            )

            if (
                continuation_enabled
                and hedge_engine.phase == StrategyPhase.REBALANCING
                and active_pending_side is not None
            ):
                rebalance_decision = self._rebalance_planner.decide(
                    current_phase=hedge_engine.phase,
                    risk_should_reduce=risk_decision.should_reduce,
                    risk_should_pause=risk_decision.should_pause,
                    bull_mode=bull_mode,
                    allow_restore_now=resolved_allow_restore_now,
                )
                self._record_rebalance_action(
                    result=result,
                    action=rebalance_decision.action,
                )
                if rebalance_decision.action == "restore_now":
                    hedge_engine.on_rebalance_restored(selected_symbol)
                    result.pending_rebalance_side = None
                    self._apply_sim_restore(
                        runtime=runtime,
                        side=active_pending_side,
                        price=selected_price,
                    )
                    profit_bucket = replace(
                        profit_bucket,
                        closed_loop_ready=(
                            hedge_engine.phase == StrategyPhase.HEDGED
                            and result.pending_rebalance_side is None
                        ),
                        last_rebalance_action="restore_now",
                        sweep_block_reason=None
                        if hedge_engine.phase == StrategyPhase.HEDGED
                        and result.pending_rebalance_side is None
                        else "pending_rebalance",
                    )
                elif rebalance_decision.action == "reduce_risk":
                    hedge_engine.phase = StrategyPhase.PAUSED
                    result.pending_rebalance_side = None
                    self._clear_sim_positions(runtime)
                    profit_bucket = replace(
                        profit_bucket,
                        closed_loop_ready=False,
                        last_rebalance_action="reduce_risk",
                        sweep_block_reason="risk_block",
                    )
                else:
                    result.pending_rebalance_side = active_pending_side
                    profit_bucket = replace(
                        profit_bucket,
                        closed_loop_ready=False,
                        last_rebalance_action="restore_later",
                        sweep_block_reason="pending_rebalance",
                    )
                runtime.profit_bucket = profit_bucket
                result.phase = hedge_engine.phase
                continue

            if hedge_engine.phase != StrategyPhase.HEDGED:
                result.phase = hedge_engine.phase
                result.pending_rebalance_side = None
                continue

            long_unrealized = selected_row.get("long_unrealized", ZERO)
            short_unrealized = selected_row.get("short_unrealized", ZERO)
            take_profit_intent = hedge_engine.on_pnl_update(
                long_unrealized=long_unrealized,
                short_unrealized=short_unrealized,
            )
            if take_profit_intent.action != "take_profit":
                result.phase = hedge_engine.phase
                result.pending_rebalance_side = None
                continue
            harvest_decision = self._harvest_rule.evaluate(
                symbol=selected_symbol,
                side=take_profit_intent.side,
                unrealized_pnl=take_profit_intent.unrealized_pnl,
                target_notional=self._target_notional,
                min_safe_unimmr=getattr(self._risk_manager, "_soft_unimmr", ZERO),
                uni_mmr=capital_snapshot.uni_mmr if snapshot is not None else Decimal("999999"),
                recent_funding_cost=Decimal(
                    str(selected_row.get("recent_funding_cost", ZERO))
                ),
            )
            if not harvest_decision.should_harvest:
                hedge_engine.mark_hedged(selected_symbol)
                result.phase = hedge_engine.phase
                result.pending_rebalance_side = None
                continue
            result.take_profit_count += 1
            result.realized_pnl += harvest_decision.net_pnl
            profit_bucket = self._pnl_manager.record_take_profit(
                bucket=profit_bucket,
                realized_pnl=harvest_decision.net_pnl,
            )
            runtime.profit_bucket = profit_bucket
            self._apply_take_profit_close(
                runtime=runtime,
                side=take_profit_intent.side,
            )
            hedge_engine.on_take_profit_completed(
                symbol=selected_symbol,
                closed_side=take_profit_intent.side,
            )
            rebalance_decision = self._rebalance_planner.decide(
                current_phase=hedge_engine.phase,
                risk_should_reduce=risk_decision.should_reduce,
                risk_should_pause=risk_decision.should_pause,
                bull_mode=bull_mode,
                allow_restore_now=resolved_allow_restore_now,
            )
            self._record_rebalance_action(
                result=result,
                action=rebalance_decision.action,
            )
            if rebalance_decision.action == "restore_now":
                hedge_engine.on_rebalance_restored(selected_symbol)
                result.pending_rebalance_side = None
                self._apply_sim_restore(
                    runtime=runtime,
                    side=take_profit_intent.side,
                    price=selected_price,
                )
                profit_bucket = replace(
                    profit_bucket,
                    closed_loop_ready=(
                        hedge_engine.phase == StrategyPhase.HEDGED
                        and result.pending_rebalance_side is None
                    ),
                    last_rebalance_action="restore_now",
                    sweep_block_reason=None
                    if hedge_engine.phase == StrategyPhase.HEDGED
                    and result.pending_rebalance_side is None
                    else "pending_rebalance",
                )
            elif rebalance_decision.action == "reduce_risk":
                hedge_engine.phase = StrategyPhase.PAUSED
                result.pending_rebalance_side = None
                self._clear_sim_positions(runtime)
                profit_bucket = replace(
                    profit_bucket,
                    closed_loop_ready=False,
                    last_rebalance_action="reduce_risk",
                    sweep_block_reason="risk_block",
                )
            else:
                result.pending_rebalance_side = take_profit_intent.side
                profit_bucket = replace(
                    profit_bucket,
                    closed_loop_ready=False,
                    last_rebalance_action="restore_later",
                    sweep_block_reason="pending_rebalance",
                )
            runtime.profit_bucket = profit_bucket
            result.phase = hedge_engine.phase

        if (
            not risk_decision.should_pause
            and not risk_decision.should_reduce
            and result.phase == StrategyPhase.HEDGED
            and result.pending_rebalance_side is None
        ):
            sweep_plan = self._transfer_planner.plan_sweep(
                snapshot=capital_snapshot,
                bucket=profit_bucket,
            )
            if sweep_plan.should_subscribe_rwusd:
                result.profit_sweep_count += 1
                profit_bucket = self._pnl_manager.record_sweep(
                    bucket=profit_bucket,
                    sweep_amount=sweep_plan.usdt_amount,
                )
                runtime.profit_bucket = profit_bucket
        elif result.phase != StrategyPhase.IDLE:
            profit_bucket = replace(
                profit_bucket,
                closed_loop_ready=False,
                sweep_block_reason=(
                    "pending_rebalance"
                    if result.pending_rebalance_side is not None
                    else "not_hedged"
                ),
            )
            runtime.profit_bucket = profit_bucket

        if risk_decision.should_redeem_topup:
            redeem_plan = self._transfer_planner.plan_redeem(
                snapshot=capital_snapshot,
                bucket=profit_bucket,
            )
            if redeem_plan.should_redeem_rwusd:
                result.redeem_count += 1
                profit_bucket = self._record_redeem(
                    bucket=profit_bucket,
                    redeem_amount=redeem_plan.usdt_amount,
                )
                runtime.profit_bucket = profit_bucket
        return self._finalize_result(result=result, bucket=profit_bucket, runtime=runtime)

    def run_sequence(
        self,
        row_batches: list[list[BacktestRow]],
        snapshot_batches: list[PortfolioSnapshot | dict],
        elapsed_hours: Decimal = ZERO,
        allow_restore_now_batches: list[bool] | None = None,
        minute_of_day_batches: list[int | None] | None = None,
    ) -> BacktestResult:
        aggregate = BacktestResult()
        rolling_runtime_state = BacktestRuntimeState()

        for index, rows in enumerate(row_batches):
            snapshot = snapshot_batches[index] if index < len(snapshot_batches) else None
            allow_restore_now = (
                allow_restore_now_batches[index]
                if allow_restore_now_batches is not None and index < len(allow_restore_now_batches)
                else None
            )
            minute_of_day = (
                minute_of_day_batches[index]
                if minute_of_day_batches is not None and index < len(minute_of_day_batches)
                else None
            )
            cycle_result = self.run(
                rows=rows,
                snapshot=snapshot,
                elapsed_hours=elapsed_hours if index > 0 else ZERO,
                allow_restore_now=allow_restore_now,
                minute_of_day=minute_of_day,
                runtime_state=rolling_runtime_state,
            )
            aggregate.selected_symbols.extend(cycle_result.selected_symbols)
            aggregate.realized_pnl += cycle_result.realized_pnl
            aggregate.take_profit_count += cycle_result.take_profit_count
            aggregate.restore_now_count += cycle_result.restore_now_count
            aggregate.restore_later_count += cycle_result.restore_later_count
            aggregate.reduce_risk_count += cycle_result.reduce_risk_count
            aggregate.profit_sweep_count += cycle_result.profit_sweep_count
            aggregate.redeem_count += cycle_result.redeem_count
            for action, count in cycle_result.rebalance_action_counts.items():
                aggregate.rebalance_action_counts[action] = (
                    aggregate.rebalance_action_counts.get(action, 0) + count
                )
            rolling_runtime_state = (
                cycle_result.runtime_state
                if cycle_result.runtime_state is not None
                else self._build_runtime_state(
                    current_symbol=cycle_result.current_symbol,
                    phase=cycle_result.phase,
                    pending_rebalance_side=cycle_result.pending_rebalance_side,
                    last_symbol_switch_minute=cycle_result.last_symbol_switch_minute,
                    profit_bucket=cycle_result.bucket,
                )
            )

        aggregate.current_symbol = rolling_runtime_state.current_symbol
        aggregate.phase = rolling_runtime_state.phase
        aggregate.pending_rebalance_side = rolling_runtime_state.pending_rebalance_side
        aggregate.last_symbol_switch_minute = rolling_runtime_state.last_symbol_switch_minute
        return self._finalize_result(
            result=aggregate,
            bucket=rolling_runtime_state.profit_bucket,
            runtime=rolling_runtime_state,
        )

    def _seed_hedge_engine(
        self,
        hedge_engine: HedgeEngine,
        selected_symbol: str,
        current_symbol: str | None,
        current_phase: StrategyPhase,
    ) -> None:
        if current_symbol == selected_symbol:
            hedge_engine.symbol = selected_symbol
            hedge_engine.phase = current_phase
            if current_phase == StrategyPhase.IDLE:
                hedge_engine.on_symbol_selected(selected_symbol)
                hedge_engine.mark_hedged(selected_symbol)
            return
        hedge_engine.on_symbol_selected(selected_symbol)
        hedge_engine.mark_hedged(selected_symbol)

    def _record_rebalance_action(
        self,
        result: BacktestResult,
        action: str,
    ) -> None:
        if action == "restore_now":
            result.restore_now_count += 1
        elif action == "restore_later":
            result.restore_later_count += 1
        elif action == "reduce_risk":
            result.reduce_risk_count += 1
        result.rebalance_action_counts[action] = (
            result.rebalance_action_counts.get(action, 0) + 1
        )

    def _resolve_allow_restore_now(
        self,
        allow_restore_now: bool | None,
        minute_of_day: int | None,
    ) -> bool:
        if allow_restore_now is not None:
            return allow_restore_now
        if minute_of_day is None:
            return True
        return minute_of_day % self._selector._eval_interval_minutes == 0

    def _hold_current_symbol_during_active_cycle(
        self,
        selector_snapshot,
        current_symbol: str | None,
        current_phase: StrategyPhase,
        pending_rebalance_side: PositionSide | None,
    ):
        if current_symbol is None:
            return selector_snapshot
        should_hold = current_phase in (
            StrategyPhase.OPENING_HEDGE,
            StrategyPhase.TAKING_PROFIT,
            StrategyPhase.HEDGED,
        ) or (
            current_phase == StrategyPhase.REBALANCING
            and pending_rebalance_side is not None
        )
        if not should_hold:
            return selector_snapshot
        selector_snapshot.selected_symbol = current_symbol
        selector_snapshot.selected_symbols = [current_symbol]
        return selector_snapshot

    def _resolve_last_switch_minute(
        self,
        current_symbol: str | None,
        selected_symbol: str | None,
        last_switch_minute: int | None,
        minute_of_day: int | None,
    ) -> int | None:
        if selected_symbol is None or selected_symbol == current_symbol or minute_of_day is None:
            return last_switch_minute
        return minute_of_day

    def _coerce_snapshot(
        self,
        snapshot: PortfolioSnapshot | dict | None,
    ) -> PortfolioSnapshot:
        if isinstance(snapshot, PortfolioSnapshot):
            return snapshot
        if snapshot is None:
            return PortfolioSnapshot(account_equity=ZERO)
        return PortfolioSnapshot(**snapshot)

    def _coerce_bucket(
        self,
        bucket: ProfitBucket | dict | None,
    ) -> ProfitBucket:
        if isinstance(bucket, ProfitBucket):
            return bucket
        if bucket is None:
            return ProfitBucket()
        return ProfitBucket(**bucket)

    def _evaluate_risk(
        self,
        snapshot: PortfolioSnapshot,
        current_drawdown: Decimal,
    ) -> RiskDecision:
        if snapshot.account_equity == ZERO and snapshot.uni_mmr == ZERO:
            return RiskDecision(
                should_pause=False,
                should_reduce=False,
                reason=None,
                should_redeem_topup=False,
            )
        return self._risk_manager.evaluate(
            snapshot=snapshot,
            current_drawdown=current_drawdown,
        )

    def _record_redeem(
        self,
        bucket: ProfitBucket,
        redeem_amount: Decimal,
    ) -> ProfitBucket:
        return ProfitBucket(
            realized_pnl_total=bucket.realized_pnl_total,
            realized_pnl_available_for_deposit=bucket.realized_pnl_available_for_deposit,
            harvest_buffer=bucket.harvest_buffer,
            rwusd_principal=max(ZERO, bucket.rwusd_principal - redeem_amount),
            rwusd_interest_accrued=bucket.rwusd_interest_accrued,
            rwusd_redeemable=max(ZERO, bucket.rwusd_redeemable - redeem_amount),
            harvest_count=bucket.harvest_count,
            deposit_count=bucket.deposit_count,
            redeem_count=bucket.redeem_count + 1,
            closed_loop_ready=bucket.closed_loop_ready,
            last_rebalance_action=bucket.last_rebalance_action,
            sweep_block_reason=bucket.sweep_block_reason,
        )

    def _new_hedge_engine(self) -> HedgeEngine:
        return HedgeEngine(
            target_notional=self._target_notional,
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
        )

    def _finalize_result(
        self,
        result: BacktestResult,
        bucket: ProfitBucket,
        runtime: BacktestRuntimeState | None = None,
    ) -> BacktestResult:
        result.bucket = bucket
        result.rwusd_principal = bucket.rwusd_principal
        result.rwusd_interest_accrued = bucket.rwusd_interest_accrued
        if runtime is None:
            runtime = self._build_runtime_state(
                current_symbol=result.current_symbol,
                phase=result.phase,
                pending_rebalance_side=result.pending_rebalance_side,
                last_symbol_switch_minute=result.last_symbol_switch_minute,
                profit_bucket=bucket,
            )
        else:
            runtime.current_symbol = result.current_symbol
            runtime.phase = result.phase
            runtime.pending_rebalance_side = result.pending_rebalance_side
            runtime.last_symbol_switch_minute = result.last_symbol_switch_minute
            runtime.profit_bucket = bucket
        result.runtime_state = runtime
        return result

    def _build_runtime_state(
        self,
        current_symbol: str | None,
        phase: StrategyPhase,
        pending_rebalance_side: PositionSide | None,
        last_symbol_switch_minute: int | None,
        profit_bucket: ProfitBucket,
    ) -> BacktestRuntimeState:
        return BacktestRuntimeState(
            current_symbol=current_symbol,
            phase=phase,
            pending_rebalance_side=pending_rebalance_side,
            last_symbol_switch_minute=last_symbol_switch_minute,
            profit_bucket=profit_bucket,
        )

    def _resolve_runtime_state(
        self,
        runtime_state: BacktestRuntimeState | None,
        bucket: ProfitBucket | dict | None,
        current_symbol: str | None,
        current_phase: StrategyPhase,
        pending_rebalance_side: PositionSide | None,
        last_switch_minute: int | None,
    ) -> BacktestRuntimeState:
        if runtime_state is not None:
            return BacktestRuntimeState(
                current_symbol=runtime_state.current_symbol,
                phase=runtime_state.phase,
                pending_rebalance_side=runtime_state.pending_rebalance_side,
                last_symbol_switch_minute=runtime_state.last_symbol_switch_minute,
                profit_bucket=self._coerce_bucket(runtime_state.profit_bucket),
                sim_long_qty=runtime_state.sim_long_qty,
                sim_short_qty=runtime_state.sim_short_qty,
                sim_long_entry_price=runtime_state.sim_long_entry_price,
                sim_short_entry_price=runtime_state.sim_short_entry_price,
                sim_long_unrealized_pnl=runtime_state.sim_long_unrealized_pnl,
                sim_short_unrealized_pnl=runtime_state.sim_short_unrealized_pnl,
                sim_last_mark_price=runtime_state.sim_last_mark_price,
                sim_take_profit_count=runtime_state.sim_take_profit_count,
                sim_restore_count=runtime_state.sim_restore_count,
            )
        return self._build_runtime_state(
            current_symbol=current_symbol,
            phase=current_phase,
            pending_rebalance_side=pending_rebalance_side,
            last_symbol_switch_minute=last_switch_minute,
            profit_bucket=self._coerce_bucket(bucket),
        )

    def _initialize_sim_hedge(
        self,
        runtime: BacktestRuntimeState,
        selected_symbol: str,
        price: Decimal,
    ) -> None:
        quantity = self._simulate_quantity(price=price)
        runtime.current_symbol = selected_symbol
        runtime.sim_long_qty = quantity
        runtime.sim_short_qty = quantity
        runtime.sim_long_entry_price = price
        runtime.sim_short_entry_price = price
        runtime.sim_long_unrealized_pnl = ZERO
        runtime.sim_short_unrealized_pnl = ZERO
        runtime.sim_last_mark_price = price

    def _update_sim_mark(
        self,
        runtime: BacktestRuntimeState,
        mark_price: Decimal,
        long_unrealized: Decimal | None,
        short_unrealized: Decimal | None,
    ) -> None:
        runtime.sim_last_mark_price = mark_price
        if long_unrealized is not None:
            runtime.sim_long_unrealized_pnl = Decimal(str(long_unrealized))
        if short_unrealized is not None:
            runtime.sim_short_unrealized_pnl = Decimal(str(short_unrealized))

    def _apply_take_profit_close(
        self,
        runtime: BacktestRuntimeState,
        side: PositionSide | None,
    ) -> None:
        if side == PositionSide.LONG:
            runtime.sim_long_qty = ZERO
            runtime.sim_long_unrealized_pnl = ZERO
        elif side == PositionSide.SHORT:
            runtime.sim_short_qty = ZERO
            runtime.sim_short_unrealized_pnl = ZERO
        runtime.sim_take_profit_count += 1

    def _apply_sim_restore(
        self,
        runtime: BacktestRuntimeState,
        side: PositionSide | None,
        price: Decimal,
    ) -> None:
        quantity = self._simulate_quantity(price=price)
        if side == PositionSide.LONG:
            runtime.sim_long_qty = quantity
            runtime.sim_long_entry_price = price
            runtime.sim_long_unrealized_pnl = ZERO
        elif side == PositionSide.SHORT:
            runtime.sim_short_qty = quantity
            runtime.sim_short_entry_price = price
            runtime.sim_short_unrealized_pnl = ZERO
        runtime.sim_last_mark_price = price
        runtime.sim_restore_count += 1

    def _clear_sim_positions(
        self,
        runtime: BacktestRuntimeState,
    ) -> None:
        runtime.sim_long_qty = ZERO
        runtime.sim_short_qty = ZERO
        runtime.sim_long_unrealized_pnl = ZERO
        runtime.sim_short_unrealized_pnl = ZERO

    def _simulate_quantity(
        self,
        price: Decimal,
    ) -> Decimal:
        if price <= ZERO:
            return ZERO
        return self._target_notional / price
