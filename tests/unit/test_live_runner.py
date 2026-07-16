from dataclasses import replace
from decimal import Decimal
import unittest

from src.app.live_runner import LiveRunner
from src.domain.models import PortfolioSnapshot, ProfitBucket
from src.domain.enums import PositionSide, StrategyPhase
from src.exchange.binance_account import UmHedgePosition
from src.infra.logging import InMemoryLogger
from src.portfolio.state import HedgeState
from src.portfolio.transfers import RedeemPlan, SweepPlan, TransferPlanner
from src.risk.rules import RiskDecision, RiskRuleSet
from src.strategy.position_sizing import OrderSizingRule


class FakeAccountService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | bool]] = []
        self.rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )
        self.remote_positions: list[UmHedgePosition] = []

    def set_um_position_mode(self, dual_side_position: bool) -> dict:
        self.calls.append(("set_um_position_mode", dual_side_position))
        return {"ok": True}

    def get_symbol_order_sizing_rule(self, symbol: str) -> OrderSizingRule:
        return self.rule

    def place_um_order(
        self,
        symbol: str,
        side: str,
        position_side: str,
        order_type: str,
        quantity: str,
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
    ) -> dict:
        payload = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": quantity,
        }
        if price is not None:
            payload["price"] = price
        if time_in_force is not None:
            payload["timeInForce"] = time_in_force
        if reduce_only is not None:
            payload["reduceOnly"] = reduce_only
        self.calls.append(("place_um_order", payload))
        return payload

    def close_position(
        self,
        symbol: str,
        position_side: str,
        quantity: str,
    ) -> dict:
        payload = {
            "symbol": symbol,
            "positionSide": position_side,
            "quantity": quantity,
        }
        self.calls.append(("close_position", payload))
        return payload

    def get_um_hedge_positions(self) -> list[UmHedgePosition]:
        self.calls.append(("get_um_hedge_positions", {}))
        return list(self.remote_positions)

    def transfer_pm_to_spot(self, asset: str, amount: str) -> dict:
        payload = {"asset": asset, "amount": amount}
        self.calls.append(("transfer_pm_to_spot", payload))
        return payload

    def transfer_spot_to_pm(self, asset: str, amount: str) -> dict:
        payload = {"asset": asset, "amount": amount}
        self.calls.append(("transfer_spot_to_pm", payload))
        return payload

    def subscribe_rwusd(self, amount: str) -> dict:
        payload = {"amount": amount}
        self.calls.append(("subscribe_rwusd", payload))
        return payload

    def redeem_rwusd(self, amount: str) -> dict:
        payload = {"amount": amount}
        self.calls.append(("redeem_rwusd", payload))
        return payload


class FakeStreamClient:
    def __init__(self, parsed_event: dict | list[dict | None] | None = None) -> None:
        self.parsed_event = parsed_event
        self.calls: list[dict] = []
        self.loop_calls: list[dict] = []

    def parse_user_stream_event(self, payload: dict) -> dict | None:
        self.calls.append(payload)
        if isinstance(self.parsed_event, list):
            if not self.parsed_event:
                return None
            return self.parsed_event.pop(0)
        return self.parsed_event

    def run_user_stream_loop(
        self,
        event_source,
        handler,
        keepalive_every: int = 50,
        max_events: int | None = None,
        retry_attempts: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ) -> int:
        self.loop_calls.append(
            {
                "keepalive_every": keepalive_every,
                "max_events": max_events,
                "retry_attempts": retry_attempts,
                "retry_backoff_seconds": retry_backoff_seconds,
                "retry_backoff_multiplier": retry_backoff_multiplier,
            }
        )
        events = list(event_source("fake-listen-key"))
        if max_events is not None:
            events = events[:max_events]
        for payload in events:
            handler(payload)
        return len(events)


class FakeStateStore:
    def __init__(self, initial_state: HedgeState | None = None) -> None:
        self.state = initial_state
        self.saved: list[HedgeState] = []
        self.saved_snapshots: list[HedgeState] = []

    def save_hedge_state(self, state: HedgeState) -> None:
        self.state = state
        self.saved.append(state)
        self.saved_snapshots.append(replace(state))

    def load_hedge_state(self) -> HedgeState | None:
        return self.state


class FakeRiskManager:
    def __init__(
        self,
        decision: RiskDecision,
        soft_unimmr: Decimal = Decimal("6"),
    ) -> None:
        self.decision = decision
        self._soft_unimmr = soft_unimmr

    def evaluate(self, snapshot: PortfolioSnapshot, current_drawdown: Decimal) -> RiskDecision:
        return self.decision


class FakeTransferPlanner:
    def __init__(
        self,
        plan: SweepPlan,
        redeem_plan: RedeemPlan | None = None,
    ) -> None:
        self.plan = plan
        self.redeem_plan = redeem_plan or RedeemPlan(
            usdt_amount=Decimal("0"),
            should_redeem_rwusd=False,
        )
        self.sweep_calls: list[dict] = []
        self.redeem_calls: list[dict] = []

    def plan_sweep(self, snapshot: PortfolioSnapshot, bucket: ProfitBucket) -> SweepPlan:
        self.sweep_calls.append({"snapshot": snapshot, "bucket": bucket})
        return self.plan

    def plan_redeem(self, snapshot: PortfolioSnapshot, bucket: ProfitBucket) -> RedeemPlan:
        self.redeem_calls.append({"snapshot": snapshot, "bucket": bucket})
        return self.redeem_plan


class FakePnlManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_take_profit(
        self,
        bucket: ProfitBucket,
        realized_pnl: Decimal,
    ) -> ProfitBucket:
        self.calls.append({"realized_pnl": realized_pnl})
        return ProfitBucket(
            realized_pnl_total=bucket.realized_pnl_total + realized_pnl,
            realized_pnl_available_for_deposit=(
                bucket.realized_pnl_available_for_deposit + realized_pnl
            ),
            harvest_buffer=bucket.harvest_buffer + realized_pnl,
            rwusd_principal=bucket.rwusd_principal,
            rwusd_interest_accrued=bucket.rwusd_interest_accrued,
            rwusd_redeemable=bucket.rwusd_redeemable,
            harvest_count=bucket.harvest_count + 1,
            deposit_count=bucket.deposit_count,
            redeem_count=bucket.redeem_count,
            closed_loop_ready=False,
            last_rebalance_action="take_profit",
            sweep_block_reason="pending_rebalance",
        )

    def record_sweep(
        self,
        bucket: ProfitBucket,
        sweep_amount: Decimal,
    ) -> ProfitBucket:
        self.calls.append({"sweep_amount": sweep_amount})
        return ProfitBucket(
            realized_pnl_total=bucket.realized_pnl_total,
            realized_pnl_available_for_deposit=(
                bucket.realized_pnl_available_for_deposit - sweep_amount
            ),
            harvest_buffer=bucket.harvest_buffer - sweep_amount,
            rwusd_principal=bucket.rwusd_principal + sweep_amount,
            rwusd_interest_accrued=bucket.rwusd_interest_accrued,
            rwusd_redeemable=bucket.rwusd_redeemable + sweep_amount,
            harvest_count=bucket.harvest_count,
            deposit_count=bucket.deposit_count + 1,
            redeem_count=bucket.redeem_count,
            closed_loop_ready=True,
            last_rebalance_action="sweep",
            sweep_block_reason=None,
        )


class FakeRebalanceDecision:
    def __init__(self, action: str) -> None:
        self.action = action


class FakeRebalancePlanner:
    def __init__(self, action: str) -> None:
        self.action = action
        self.calls: list[dict] = []

    def decide(
        self,
        current_phase,
        risk_should_reduce: bool,
        risk_should_pause: bool,
        bull_mode: bool,
        allow_restore_now: bool = True,
    ):
        self.calls.append(
            {
                "current_phase": current_phase,
                "risk_should_reduce": risk_should_reduce,
                "risk_should_pause": risk_should_pause,
                "bull_mode": bull_mode,
                "allow_restore_now": allow_restore_now,
            }
        )
        return FakeRebalanceDecision(self.action)


class FakeSelector:
    def __init__(self, selected_symbol: str | None) -> None:
        self.selected_symbol = selected_symbol
        self.calls: list[dict] = []

    def select(
        self,
        current_symbol: str | None,
        rows: list[dict],
        minute_of_day: int | None = None,
        last_switch_minute: int | None = None,
    ):
        self.calls.append(
            {
                "current_symbol": current_symbol,
                "rows": rows,
                "minute_of_day": minute_of_day,
                "last_switch_minute": last_switch_minute,
            }
        )

        class Snapshot:
            def __init__(self, selected_symbol: str | None) -> None:
                self.selected_symbol = selected_symbol
                self.scores = []

        return Snapshot(self.selected_symbol)


class LiveRunnerTests(unittest.TestCase):
    def test_live_runner_passes_selector_eval_interval_to_symbol_selector(self) -> None:
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                plan=SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            selector_eval_interval_minutes=5,
        )

        self.assertEqual(runner._selector._eval_interval_minutes, 5)

    def test_run_cycle_executes_profit_sweep_when_not_dry_run(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
            logger=logger,
            pnl_manager=pnl_manager,
        )
        runner._profit_bucket = ProfitBucket(
            realized_pnl_total=Decimal("120"),
            realized_pnl_available_for_deposit=Decimal("120"),
        )
        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()
        logger.records.clear()

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(
            account_service.calls[:2],
            [
                ("transfer_pm_to_spot", {"asset": "USDT", "amount": "120"}),
                ("subscribe_rwusd", {"amount": "120"}),
            ],
        )
        self.assertEqual(pnl_manager.calls[0]["sweep_amount"], Decimal("120"))
        self.assertEqual(runner._profit_bucket.realized_pnl_available_for_deposit, Decimal("0"))
        self.assertEqual(runner._profit_bucket.rwusd_principal, Decimal("120"))
        self.assertIn(
            "live.profit_sweep_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_redeem_topup_when_not_dry_run(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False),
            redeem_plan=RedeemPlan(
                usdt_amount=Decimal("80"),
                should_redeem_rwusd=True,
            ),
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=False,
                    should_reduce=True,
                    reason="uni_mmr_soft_limit",
                    should_redeem_topup=True,
                )
            ),
            transfer_planner=transfer_planner,
            logger=logger,
        )
        runner._profit_bucket = ProfitBucket(
            rwusd_principal=Decimal("80"),
            rwusd_redeemable=Decimal("80"),
        )
        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()
        logger.records.clear()

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("280"),
                uni_mmr=Decimal("5.5"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(
            account_service.calls[:2],
            [
                ("redeem_rwusd", {"amount": "80"}),
                ("transfer_spot_to_pm", {"asset": "USDT", "amount": "80"}),
            ],
        )
        self.assertIn(
            "live.redeem_topup_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_profit_sweep_when_dry_run_updates_bucket(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                plan=SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
        )
        runner._engine.mark_hedged("BTCUSDT")
        runner._state.symbol = "BTCUSDT"
        runner._state.phase = StrategyPhase.HEDGED
        runner._profit_bucket = ProfitBucket(
            realized_pnl_total=Decimal("120"),
            realized_pnl_available_for_deposit=Decimal("120"),
            harvest_buffer=Decimal("120"),
            rwusd_principal=Decimal("20"),
            rwusd_redeemable=Decimal("20"),
            deposit_count=2,
            closed_loop_ready=True,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(account_service.calls, [])
        self.assertEqual(pnl_manager.calls[0]["sweep_amount"], Decimal("120"))
        self.assertEqual(
            runner.profit_bucket.realized_pnl_available_for_deposit,
            Decimal("0"),
        )
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("140"))
        self.assertEqual(runner.profit_bucket.rwusd_redeemable, Decimal("140"))
        self.assertEqual(runner.profit_bucket.deposit_count, 3)
        self.assertIn(
            "live.profit_sweep_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_redeem_topup_when_dry_run_updates_bucket(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=False,
                    should_reduce=True,
                    reason="uni_mmr_soft_limit",
                    should_redeem_topup=True,
                )
            ),
            transfer_planner=FakeTransferPlanner(
                plan=SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False),
                redeem_plan=RedeemPlan(
                    usdt_amount=Decimal("80"),
                    should_redeem_rwusd=True,
                ),
            ),
            logger=logger,
        )
        runner._profit_bucket = ProfitBucket(
            realized_pnl_total=Decimal("120"),
            realized_pnl_available_for_deposit=Decimal("15"),
            rwusd_principal=Decimal("200"),
            rwusd_interest_accrued=Decimal("5"),
            rwusd_redeemable=Decimal("150"),
            harvest_count=4,
            deposit_count=3,
            redeem_count=1,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("280"),
                uni_mmr=Decimal("5.5"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(account_service.calls, [])
        self.assertEqual(runner.profit_bucket.realized_pnl_total, Decimal("120"))
        self.assertEqual(
            runner.profit_bucket.realized_pnl_available_for_deposit,
            Decimal("15"),
        )
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("120"))
        self.assertEqual(runner.profit_bucket.rwusd_redeemable, Decimal("70"))
        self.assertEqual(runner.profit_bucket.rwusd_interest_accrued, Decimal("5"))
        self.assertEqual(runner.profit_bucket.harvest_count, 4)
        self.assertEqual(runner.profit_bucket.deposit_count, 3)
        self.assertEqual(runner.profit_bucket.redeem_count, 2)
        self.assertIn(
            "live.redeem_topup_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_logs_profit_sweep_plan_when_bucket_is_sweepable(self) -> None:
        logger = InMemoryLogger()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
            logger=logger,
        )
        runner._engine.mark_hedged("BTCUSDT")
        runner._state.symbol = "BTCUSDT"
        runner._state.phase = StrategyPhase.HEDGED
        runner._profit_bucket = ProfitBucket(
            realized_pnl_total=Decimal("120"),
            realized_pnl_available_for_deposit=Decimal("120"),
            harvest_buffer=Decimal("120"),
            closed_loop_ready=True,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        sweep_records = [record for record in logger.records if record.event == "live.profit_sweep_plan"]
        self.assertEqual(len(sweep_records), 1)
        self.assertEqual(sweep_records[0].context["usdt_amount"], "120")
        self.assertTrue(sweep_records[0].context["should_subscribe_rwusd"])
        self.assertEqual(len(transfer_planner.sweep_calls), 1)

    def test_run_cycle_logs_redeem_topup_plan_when_risk_requires_collateral(self) -> None:
        logger = InMemoryLogger()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False),
            redeem_plan=RedeemPlan(
                usdt_amount=Decimal("80"),
                should_redeem_rwusd=True,
            ),
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=False,
                    should_reduce=True,
                    reason="uni_mmr_soft_limit",
                    should_redeem_topup=True,
                )
            ),
            transfer_planner=transfer_planner,
            logger=logger,
        )
        runner._profit_bucket = ProfitBucket(
            rwusd_principal=Decimal("80"),
            rwusd_redeemable=Decimal("80"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("280"),
                uni_mmr=Decimal("5.5"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        redeem_records = [record for record in logger.records if record.event == "live.redeem_topup_plan"]
        self.assertEqual(len(redeem_records), 1)
        self.assertEqual(redeem_records[0].context["usdt_amount"], "80")
        self.assertTrue(redeem_records[0].context["should_redeem_rwusd"])
        self.assertEqual(len(transfer_planner.redeem_calls), 1)

    def test_run_cycle_executes_redeem_topup_when_available_balance_is_below_reserve(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=RiskRuleSet(
                soft_unimmr=Decimal("12"),
                hard_unimmr=Decimal("8"),
                max_drawdown=Decimal("0.15"),
                redeem_unimmr=Decimal("10"),
                reserve_available_balance=Decimal("300"),
            ),
            transfer_planner=TransferPlanner(
                min_sweep=Decimal("25"),
                pm_reserve=Decimal("300"),
                min_redeem=Decimal("10"),
                redeem_unimmr=Decimal("10"),
            ),
            logger=logger,
        )
        runner._profit_bucket = ProfitBucket(
            rwusd_principal=Decimal("120"),
            rwusd_redeemable=Decimal("120"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("260"),
                uni_mmr=Decimal("14"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(
            account_service.calls[:2],
            [
                ("redeem_rwusd", {"amount": "40"}),
                ("transfer_spot_to_pm", {"asset": "USDT", "amount": "40"}),
            ],
        )
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("80"))
        self.assertEqual(runner.profit_bucket.rwusd_redeemable, Decimal("80"))
        self.assertEqual(runner.profit_bucket.redeem_count, 1)
        self.assertIn(
            "live.redeem_topup_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_precise_redeem_topup_when_unimmr_gap_exceeds_reserve_gap(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=RiskRuleSet(
                soft_unimmr=Decimal("12"),
                hard_unimmr=Decimal("5"),
                max_drawdown=Decimal("0.15"),
                redeem_unimmr=Decimal("6"),
                reserve_available_balance=Decimal("300"),
            ),
            transfer_planner=TransferPlanner(
                min_sweep=Decimal("25"),
                pm_reserve=Decimal("300"),
                min_redeem=Decimal("10"),
                redeem_unimmr=Decimal("6"),
            ),
            logger=logger,
        )
        runner._profit_bucket = ProfitBucket(
            rwusd_principal=Decimal("200"),
            rwusd_redeemable=Decimal("200"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("290"),
                uni_mmr=Decimal("5.5"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(
            account_service.calls[:2],
            [
                ("redeem_rwusd", {"amount": "90.91"}),
                ("transfer_spot_to_pm", {"asset": "USDT", "amount": "90.91"}),
            ],
        )
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("109.09"))
        self.assertEqual(runner.profit_bucket.rwusd_redeemable, Decimal("109.09"))
        self.assertEqual(runner.profit_bucket.redeem_count, 1)
        self.assertIn(
            "live.redeem_topup_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_returns_rebalance_intent_after_take_profit_when_restore_now(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        rebalance_planner = FakeRebalancePlanner(action="restore_now")
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=rebalance_planner,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(result.intent.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(pnl_manager.calls[0]["realized_pnl"], Decimal("28"))
        self.assertEqual(runner.profit_bucket.realized_pnl_total, Decimal("28"))
        self.assertEqual(state_store.state.phase, StrategyPhase.HEDGED)
        self.assertGreater(state_store.state.sim_long_qty, Decimal("0"))
        self.assertGreater(state_store.state.sim_short_qty, Decimal("0"))
        self.assertEqual(state_store.state.sim_take_profit_count, 1)
        self.assertEqual(state_store.state.sim_restore_count, 1)
        self.assertTrue(
            any(snapshot.sim_long_qty == Decimal("0") for snapshot in state_store.saved_snapshots)
        )
        self.assertTrue(
            any(snapshot.phase == StrategyPhase.REBALANCING for snapshot in state_store.saved_snapshots)
        )
        rebalance_records = [
            record for record in logger.records if record.event == "live.rebalance_decision"
        ]
        self.assertEqual(len(rebalance_records), 1)
        self.assertEqual(rebalance_records[0].context["action"], "restore_now")
        self.assertIn(
            "live.execution_route",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_profit_sweep_after_restore_now_when_cycle_returns_to_hedged(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("28"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=FakeRebalancePlanner(action="restore_now"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(len(transfer_planner.sweep_calls), 1)
        self.assertEqual(pnl_manager.calls[1]["sweep_amount"], Decimal("28"))
        self.assertEqual(runner.profit_bucket.realized_pnl_available_for_deposit, Decimal("0"))
        self.assertEqual(runner.profit_bucket.harvest_buffer, Decimal("0"))
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("28"))
        self.assertTrue(runner.profit_bucket.closed_loop_ready)
        self.assertEqual(runner.profit_bucket.last_rebalance_action, "sweep")
        self.assertIsNone(runner.profit_bucket.sweep_block_reason)
        self.assertIn(
            "live.profit_sweep_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_executes_restore_now_missing_leg_when_not_dry_run(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        rebalance_planner = FakeRebalancePlanner(action="restore_now")
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=rebalance_planner,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                ),
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "MARKET",
                        "quantity": "0.016",
                    },
                )
            ],
        )

    def test_run_cycle_defers_profit_sweep_until_after_take_profit_rebalance_cycle(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("28"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=FakeRebalancePlanner(action="restore_now"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()
        logger.records.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                ),
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "MARKET",
                        "quantity": "0.016",
                    },
                ),
            ],
        )
        self.assertEqual(len(transfer_planner.sweep_calls), 0)
        self.assertEqual(runner._profit_bucket.realized_pnl_available_for_deposit, Decimal("28"))
        self.assertEqual(runner._profit_bucket.harvest_buffer, Decimal("28"))
        self.assertEqual(runner._profit_bucket.rwusd_principal, Decimal("0"))
        self.assertFalse(runner._profit_bucket.closed_loop_ready)
        self.assertEqual(runner._profit_bucket.last_rebalance_action, "restore_now")
        self.assertEqual(runner._profit_bucket.sweep_block_reason, "pending_rebalance")
        self.assertNotIn(
            "live.profit_sweep_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_restores_missing_leg_on_usdc_symbol_when_available(self) -> None:
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["ETHUSDT", "ETHUSDC"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            pnl_manager=FakePnlManager(),
            rebalance_planner=FakeRebalancePlanner(action="restore_now"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="ETHUSDT", position_side="LONG")
        runner.on_order_filled(symbol="ETHUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(runner._state.long_symbol, "ETHUSDC")
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "ETHUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.625",
                    },
                ),
                (
                    "place_um_order",
                    {
                        "symbol": "ETHUSDC",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "LIMIT",
                        "quantity": "0.625",
                        "price": "1600",
                        "timeInForce": "GTX",
                        "reduceOnly": False,
                    },
                ),
            ],
        )

    def test_run_cycle_keeps_hedged_when_take_profit_close_order_cannot_be_sized(self) -> None:
        pnl_manager = FakePnlManager()
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            pnl_manager=pnl_manager,
            rebalance_planner=FakeRebalancePlanner(action="restore_now"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()
        account_service.rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("2000"),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertTrue(runner._state.long_filled)
        self.assertTrue(runner._state.short_filled)
        self.assertEqual(pnl_manager.calls, [])
        self.assertEqual(runner.profit_bucket.realized_pnl_total, Decimal("0"))
        self.assertEqual(account_service.calls, [])

    def test_run_cycle_executes_reduce_risk_by_closing_remaining_leg(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        rebalance_planner = FakeRebalancePlanner(action="reduce_risk")
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=True, reason="uni_mmr_soft_limit")
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=rebalance_planner,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.PAUSED)
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                ),
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "SHORT",
                        "quantity": "0.016",
                    },
                )
            ],
        )
        self.assertFalse(runner.profit_bucket.closed_loop_ready)
        self.assertEqual(runner.profit_bucket.last_rebalance_action, "reduce_risk")
        self.assertEqual(runner.profit_bucket.sweep_block_reason, "risk_block")

    def test_run_cycle_blocks_profit_sweep_after_take_profit_when_restore_later(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        transfer_planner = FakeTransferPlanner(
            plan=SweepPlan(usdt_amount=Decimal("28"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=FakeRebalancePlanner(action="restore_later"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        logger.records.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(len(transfer_planner.sweep_calls), 0)
        self.assertEqual(runner.profit_bucket.realized_pnl_available_for_deposit, Decimal("28"))
        self.assertEqual(runner.profit_bucket.harvest_buffer, Decimal("28"))
        self.assertFalse(runner.profit_bucket.closed_loop_ready)
        self.assertEqual(runner.profit_bucket.last_rebalance_action, "restore_later")
        self.assertEqual(runner.profit_bucket.sweep_block_reason, "pending_rebalance")
        self.assertNotIn(
            "live.profit_sweep_executed",
            [record.event for record in logger.records],
        )

    def test_run_cycle_keeps_rebalancing_without_restore_order_when_restore_later(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        rebalance_planner = FakeRebalancePlanner(action="restore_later")
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=rebalance_planner,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                )
            ],
        )

    def test_run_cycle_restores_missing_leg_on_later_cycle_after_restore_later(self) -> None:
        logger = InMemoryLogger()
        pnl_manager = FakePnlManager()
        rebalance_planner = FakeRebalancePlanner(action="restore_later")
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
            rebalance_planner=rebalance_planner,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                }
            ]
        )
        account_service.calls.clear()
        rebalance_planner.action = "restore_now"

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60020"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                }
            ]
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(
            account_service.calls,
            [
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "MARKET",
                        "quantity": "0.016",
                    },
                )
            ],
        )

    def test_run_cycle_delays_restore_until_checkpoint_and_records_net_harvest(self) -> None:
        logger = InMemoryLogger()
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None),
                soft_unimmr=Decimal("6"),
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
            pnl_manager=pnl_manager,
        )
        selector = FakeSelector(selected_symbol="BTCUSDT")
        runner._selector = selector

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            minute_of_day=0,
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                    "recent_funding_cost": Decimal("1"),
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            minute_of_day=1,
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(selector.calls[-1]["minute_of_day"], 1)
        self.assertEqual(pnl_manager.calls[0]["realized_pnl"], Decimal("27"))
        self.assertEqual(runner.profit_bucket.realized_pnl_total, Decimal("27"))
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                )
            ],
        )
        rebalance_records = [
            record for record in logger.records if record.event == "live.rebalance_decision"
        ]
        self.assertEqual(rebalance_records[-1].context["action"], "restore_later")

    def test_run_cycle_accrues_rwusd_interest_when_elapsed_hours_provided(self) -> None:
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner._profit_bucket = ProfitBucket(rwusd_principal=Decimal("100"))

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            elapsed_hours=Decimal("24"),
        )

        self.assertGreater(runner.profit_bucket.rwusd_interest_accrued, Decimal("0"))
        self.assertEqual(runner.profit_bucket.rwusd_principal, Decimal("100"))

    def test_run_cycle_keeps_hedged_when_harvest_rule_rejects_take_profit(self) -> None:
        account_service = FakeAccountService()
        pnl_manager = FakePnlManager()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None),
                soft_unimmr=Decimal("6"),
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            pnl_manager=pnl_manager,
        )
        runner._selector = FakeSelector(selected_symbol="BTCUSDT")

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            minute_of_day=0,
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        account_service.calls.clear()

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60050"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                    "recent_funding_cost": Decimal("15"),
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("12"),
            ),
            minute_of_day=15,
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(account_service.calls, [])
        self.assertEqual(pnl_manager.calls, [])
        self.assertEqual(runner.profit_bucket.realized_pnl_total, Decimal("0"))
        self.assertTrue(runner._state.long_filled)
        self.assertTrue(runner._state.short_filled)

    def test_run_cycle_logs_risk_pause_reason_when_risk_manager_blocks(self) -> None:
        logger = InMemoryLogger()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=True,
                    should_reduce=True,
                    reason="uni_mmr_hard_limit",
                )
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("3"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertIsNone(result.selected_symbol)
        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(result.risk_reason, "uni_mmr_hard_limit")
        self.assertEqual(logger.records[-1].event, "live.risk_decision")
        self.assertEqual(logger.records[-1].context["reason"], "uni_mmr_hard_limit")
        self.assertTrue(logger.records[-1].context["should_pause"])

    def test_run_cycle_logs_selector_scores_and_selected_symbol(self) -> None:
        logger = InMemoryLogger()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("0.80"),
                    "volatility": Decimal("0.70"),
                    "funding": Decimal("0.90"),
                    "margin": Decimal("0.80"),
                    "blocked": False,
                },
                {
                    "symbol": "SOLUSDT",
                    "close": Decimal("150"),
                    "liquidity": Decimal("0.20"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.10"),
                    "margin": Decimal("0.20"),
                    "blocked": True,
                },
            ]
        )

        selector_records = [
            record for record in logger.records if record.event == "live.selector_snapshot"
        ]
        self.assertEqual(len(selector_records), 1)
        record = selector_records[0]
        self.assertEqual(record.event, "live.selector_snapshot")
        self.assertEqual(record.context["selected_symbol"], "BTCUSDT")
        self.assertEqual(record.context["current_symbol"], None)
        self.assertEqual(len(record.context["scores"]), 3)
        self.assertEqual(record.context["scores"][0]["symbol"], "BTCUSDT")
        self.assertIn("execution_cost_bps", record.context["scores"][0])
        self.assertIn("preferred_execution_symbol", record.context["scores"][0])
        self.assertEqual(record.context["scores"][2]["reject_reason"], "blocked")

    def test_run_cycle_records_last_symbol_switch_minute_when_selection_changes(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                last_symbol_switch_minute=5,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()
        runner._selector = FakeSelector(selected_symbol="ETHUSDT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.60"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.60"),
                    "margin": Decimal("0.60"),
                    "blocked": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("1.00"),
                    "volatility": Decimal("1.00"),
                    "funding": Decimal("1.00"),
                    "margin": Decimal("1.00"),
                    "blocked": False,
                },
            ],
            minute_of_day=30,
        )

        self.assertEqual(result.selected_symbol, "ETHUSDT")
        self.assertEqual(runner._selector.calls[0]["last_switch_minute"], 5)
        self.assertEqual(state_store.state.last_symbol_switch_minute, 30)

    def test_run_cycle_holds_current_symbol_while_rebalancing_missing_leg(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.REBALANCING,
                long_symbol="BTCUSDT",
                short_symbol="BTCUSDT",
                long_filled=False,
                short_filled=True,
                last_symbol_switch_minute=0,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()
        runner._pending_rebalance_side = PositionSide.LONG
        runner._selector = FakeSelector(selected_symbol="ETHUSDT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.60"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.60"),
                    "margin": Decimal("0.60"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("1.00"),
                    "volatility": Decimal("1.00"),
                    "funding": Decimal("1.00"),
                    "margin": Decimal("1.00"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                },
            ],
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(runner.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.REBALANCING)
        self.assertEqual(runner._pending_rebalance_side, PositionSide.LONG)

    def test_run_cycle_holds_current_symbol_while_hedged_position_is_active(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.HEDGED,
                long_symbol="BTCUSDT",
                short_symbol="BTCUSDT",
                long_filled=True,
                short_filled=True,
                last_symbol_switch_minute=0,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()
        runner._selector = FakeSelector(selected_symbol="ETHUSDT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.60"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.60"),
                    "margin": Decimal("0.60"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("1.00"),
                    "volatility": Decimal("1.00"),
                    "funding": Decimal("1.00"),
                    "margin": Decimal("1.00"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                },
            ],
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.symbol, "BTCUSDT")
        self.assertEqual(state_store.state.phase, StrategyPhase.HEDGED)

    def test_run_cycle_holds_current_symbol_while_opening_hedge_is_active(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.OPENING_HEDGE,
                long_symbol="BTCUSDT",
                short_symbol="BTCUSDT",
                long_filled=False,
                short_filled=False,
                last_symbol_switch_minute=0,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()
        runner._selector = FakeSelector(selected_symbol="ETHUSDT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.60"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.60"),
                    "margin": Decimal("0.60"),
                    "blocked": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("1.00"),
                    "volatility": Decimal("1.00"),
                    "funding": Decimal("1.00"),
                    "margin": Decimal("1.00"),
                    "blocked": False,
                },
            ],
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.OPENING_HEDGE)
        self.assertEqual(state_store.state.symbol, "BTCUSDT")
        self.assertEqual(state_store.state.phase, StrategyPhase.OPENING_HEDGE)

    def test_run_cycle_holds_current_symbol_while_taking_profit_is_active(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.TAKING_PROFIT,
                long_symbol="BTCUSDT",
                short_symbol="BTCUSDT",
                long_filled=True,
                short_filled=True,
                last_symbol_switch_minute=0,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()
        runner._selector = FakeSelector(selected_symbol="ETHUSDT")

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.60"),
                    "volatility": Decimal("0.60"),
                    "funding": Decimal("0.60"),
                    "margin": Decimal("0.60"),
                    "blocked": False,
                    "long_unrealized": Decimal("30"),
                    "short_unrealized": Decimal("-8"),
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("1.00"),
                    "volatility": Decimal("1.00"),
                    "funding": Decimal("1.00"),
                    "margin": Decimal("1.00"),
                    "blocked": False,
                    "long_unrealized": Decimal("5"),
                    "short_unrealized": Decimal("-2"),
                },
            ],
            minute_of_day=120,
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.TAKING_PROFIT)
        self.assertEqual(state_store.state.symbol, "BTCUSDT")
        self.assertEqual(state_store.state.phase, StrategyPhase.TAKING_PROFIT)
        self.assertEqual(state_store.state.sim_take_profit_count, 0)

    def test_run_cycle_emits_open_hedge_intent_without_orders_in_dry_run(self) -> None:
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("0.80"),
                    "volatility": Decimal("0.70"),
                    "funding": Decimal("0.90"),
                    "margin": Decimal("0.80"),
                    "blocked": False,
                },
            ]
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "open_hedge")
        self.assertEqual(account_service.calls, [])

    def test_run_cycle_dry_run_open_hedge_creates_virtual_hedge_state(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("3000"),
                    "liquidity": Decimal("0.80"),
                    "volatility": Decimal("0.70"),
                    "funding": Decimal("0.90"),
                    "margin": Decimal("0.80"),
                    "blocked": False,
                },
            ]
        )

        self.assertEqual(result.intent.action, "open_hedge")
        self.assertIsNotNone(state_store.state)
        self.assertEqual(state_store.state.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.sim_leverage, Decimal("20"))
        self.assertGreater(state_store.state.sim_long_qty, Decimal("0"))
        self.assertGreater(state_store.state.sim_short_qty, Decimal("0"))
        self.assertEqual(state_store.state.sim_long_entry_price, Decimal("60000"))
        self.assertEqual(state_store.state.sim_short_entry_price, Decimal("60000"))
        self.assertEqual(state_store.state.sim_long_unrealized_pnl, Decimal("0"))
        self.assertEqual(state_store.state.sim_short_unrealized_pnl, Decimal("0"))
        self.assertEqual(state_store.state.sim_last_mark_price, Decimal("60000"))
        self.assertEqual(state_store.state.sim_take_profit_count, 0)
        self.assertEqual(state_store.state.sim_restore_count, 0)
        self.assertEqual(state_store.state.sim_cycle_id, 0)
        self.assertTrue(state_store.state.long_filled)
        self.assertTrue(state_store.state.short_filled)

    def test_run_cycle_dry_run_marks_virtual_pnl_on_next_price_update(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "SOLUSDT",
                    "close": Decimal("150"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "SOLUSDT",
                    "close": Decimal("153"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        self.assertEqual(result.intent.action, "hold")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.sim_last_mark_price, Decimal("153"))
        self.assertGreater(state_store.state.sim_long_unrealized_pnl, Decimal("0"))
        self.assertLess(state_store.state.sim_short_unrealized_pnl, Decimal("0"))

    def test_run_cycle_uses_virtual_unrealized_for_take_profit_when_live_rows_omit_it(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            pnl_manager=FakePnlManager(),
            rebalance_planner=FakeRebalancePlanner("restore_now"),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "SOLUSDT",
                    "close": Decimal("150"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "SOLUSDT",
                    "close": Decimal("155"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("10000"),
                available_balance=Decimal("10000"),
                uni_mmr=Decimal("99999999"),
            ),
        )

        self.assertEqual(result.intent.action, "rebalance")
        self.assertEqual(result.intent.side.value, "LONG")
        self.assertEqual(state_store.state.sim_take_profit_count, 1)
        self.assertEqual(state_store.state.sim_restore_count, 1)
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

    def test_run_cycle_places_dual_orders_when_not_dry_run(self) -> None:
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        expected_quantity = "0.016"
        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "open_hedge")
        self.assertEqual(
            account_service.calls,
            [
                ("set_um_position_mode", True),
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "MARKET",
                        "quantity": expected_quantity,
                    },
                ),
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "positionSide": "SHORT",
                        "type": "MARKET",
                        "quantity": expected_quantity,
                    },
                ),
            ],
        )

    def test_run_cycle_places_initial_dual_orders_on_usdc_route_when_available(self) -> None:
        account_service = FakeAccountService()
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["ETHUSDT", "ETHUSDC"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        self.assertEqual(result.selected_symbol, "ETHUSDT")
        self.assertEqual(result.intent.action, "open_hedge")
        self.assertEqual(state_store.state.symbol, "ETHUSDT")
        self.assertEqual(state_store.state.long_symbol, "ETHUSDC")
        self.assertEqual(state_store.state.short_symbol, "ETHUSDC")
        self.assertEqual(
            account_service.calls,
            [
                ("set_um_position_mode", True),
                (
                    "place_um_order",
                    {
                        "symbol": "ETHUSDC",
                        "side": "BUY",
                        "positionSide": "LONG",
                        "type": "LIMIT",
                        "quantity": "0.625",
                        "price": "1600",
                        "timeInForce": "GTX",
                        "reduceOnly": False,
                    },
                ),
                (
                    "place_um_order",
                    {
                        "symbol": "ETHUSDC",
                        "side": "SELL",
                        "positionSide": "SHORT",
                        "type": "LIMIT",
                        "quantity": "0.625",
                        "price": "1600",
                        "timeInForce": "GTX",
                        "reduceOnly": False,
                    },
                ),
            ],
        )

    def test_usdc_open_fills_keep_anchor_symbol_for_strategy_state(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["ETHUSDT", "ETHUSDC"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        self.assertEqual(result.intent.action, "open_hedge")
        self.assertEqual(state_store.state.symbol, "ETHUSDT")
        self.assertEqual(state_store.state.long_symbol, "ETHUSDC")
        self.assertEqual(state_store.state.short_symbol, "ETHUSDC")
        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

        runner.on_order_filled(symbol="ETHUSDC", position_side="LONG")
        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

        runner.on_order_filled(symbol="ETHUSDC", position_side="SHORT")
        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.symbol, "ETHUSDT")
        self.assertEqual(state_store.state.long_symbol, "ETHUSDC")
        self.assertEqual(state_store.state.short_symbol, "ETHUSDC")

    def test_run_cycle_skips_orders_when_sizing_rule_rejects_quantity(self) -> None:
        account_service = FakeAccountService()
        account_service.rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.050"),
            min_notional=Decimal("5"),
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        self.assertEqual(result.selected_symbol, "BTCUSDT")
        self.assertEqual(result.intent.action, "open_hedge")
        self.assertEqual(account_service.calls, [])

    def test_order_update_marks_hedged_only_after_both_legs_fill(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")

        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")

        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.phase, StrategyPhase.HEDGED)
        self.assertTrue(state_store.state.long_filled)
        self.assertTrue(state_store.state.short_filled)

    def test_restore_state_recovers_symbol_and_phase(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="ETHUSDT",
                phase=StrategyPhase.HEDGED,
                long_notional=Decimal("1000"),
                short_notional=Decimal("1000"),
                long_filled=True,
                short_filled=True,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.restore_state()

        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

    def test_handle_user_stream_event_marks_fill_from_parsed_stream_payload(self) -> None:
        state_store = FakeStateStore()
        stream_client = FakeStreamClient(
            parsed_event={
                "event_type": "order_filled",
                "symbol": "BTCUSDT",
                "position_side": "LONG",
            }
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {"s": "BTCUSDT", "ps": "LONG", "X": "FILLED"},
            }
        )

        self.assertTrue(state_store.state.long_filled)
        self.assertTrue(state_store.state.short_filled)
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

    def test_handle_failed_short_leg_requests_recover_short_action(self) -> None:
        state_store = FakeStateStore()
        stream_client = FakeStreamClient(
            parsed_event={
                "event_type": "order_failed",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "status": "REJECTED",
            }
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        action = runner.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {"s": "BTCUSDT", "ps": "SHORT", "X": "REJECTED"},
            }
        )

        self.assertEqual(action["action"], "recover_missing_leg")
        self.assertEqual(action["position_side"], "SHORT")
        self.assertEqual(action["symbol"], "BTCUSDT")

    def test_handle_failed_short_leg_executes_recovery_when_not_dry_run(self) -> None:
        state_store = FakeStateStore()
        account_service = FakeAccountService()
        stream_client = FakeStreamClient(
            parsed_event={
                "event_type": "order_failed",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "status": "REJECTED",
            }
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        account_service.calls.clear()
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        action = runner.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {"s": "BTCUSDT", "ps": "SHORT", "X": "REJECTED"},
            }
        )

        self.assertEqual(action["action"], "recover_missing_leg")
        self.assertEqual(
            account_service.calls,
            [
                (
                    "place_um_order",
                    {
                        "symbol": "BTCUSDT",
                        "side": "SELL",
                        "positionSide": "SHORT",
                        "type": "MARKET",
                        "quantity": "0.016",
                    },
                )
            ],
        )

    def test_handle_failed_short_leg_executes_recovery_on_usdc_route_when_available(self) -> None:
        state_store = FakeStateStore()
        account_service = FakeAccountService()
        stream_client = FakeStreamClient(
            parsed_event={
                "event_type": "order_failed",
                "symbol": "ETHUSDC",
                "position_side": "SHORT",
                "status": "REJECTED",
            }
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=stream_client,
            candidate_symbols=["ETHUSDT", "ETHUSDC"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        account_service.calls.clear()
        runner.on_order_filled(symbol="ETHUSDC", position_side="LONG")
        action = runner.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {"s": "ETHUSDC", "ps": "SHORT", "X": "REJECTED"},
            }
        )

        self.assertEqual(action["action"], "recover_missing_leg")
        self.assertEqual(
            account_service.calls,
            [
                (
                    "place_um_order",
                    {
                        "symbol": "ETHUSDC",
                        "side": "SELL",
                        "positionSide": "SHORT",
                        "type": "LIMIT",
                        "quantity": "0.625",
                        "price": "1600",
                        "timeInForce": "GTX",
                        "reduceOnly": False,
                    },
                )
            ],
        )

    def test_handle_failed_short_leg_logs_execution_route_context(self) -> None:
        state_store = FakeStateStore()
        account_service = FakeAccountService()
        logger = InMemoryLogger()
        stream_client = FakeStreamClient(
            parsed_event={
                "event_type": "order_failed",
                "symbol": "ETHUSDC",
                "position_side": "SHORT",
                "status": "REJECTED",
            }
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=stream_client,
            candidate_symbols=["ETHUSDT", "ETHUSDC"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
            logger=logger,
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "ETHUSDT",
                    "close": Decimal("1600"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        account_service.calls.clear()
        logger.records.clear()
        runner.on_order_filled(symbol="ETHUSDC", position_side="LONG")
        runner.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {"s": "ETHUSDC", "ps": "SHORT", "X": "REJECTED"},
            }
        )

        route_records = [
            record for record in logger.records if record.event == "live.execution_route"
        ]
        self.assertEqual(len(route_records), 1)
        self.assertEqual(
            route_records[0].context,
            {
                "anchor_symbol": "ETHUSDT",
                "execution_symbol": "ETHUSDC",
                "execution_stage": "recover_missing_leg",
                "maker_only": True,
                "fallback_reason": None,
            },
        )

    def test_handle_failed_second_leg_after_first_failure_requests_reduce_only(self) -> None:
        state_store = FakeStateStore()
        stream_client = FakeStreamClient()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        first_action = runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="REJECTED",
        )
        second_action = runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="CANCELED",
        )

        self.assertEqual(first_action["action"], "recover_missing_leg")
        self.assertEqual(second_action["action"], "flatten_exposure")
        self.assertEqual(second_action["position_side"], "LONG")

    def test_handle_failed_leg_resets_failure_count_after_successful_fill(self) -> None:
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        first_action = runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="REJECTED",
        )
        runner.on_order_filled(symbol="BTCUSDT", position_side="SHORT")
        second_action = runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="REJECTED",
        )

        self.assertEqual(first_action["action"], "recover_missing_leg")
        self.assertEqual(second_action["action"], "recover_missing_leg")

    def test_handle_failed_second_leg_executes_flatten_when_not_dry_run(self) -> None:
        state_store = FakeStateStore()
        account_service = FakeAccountService()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=False,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )
        account_service.calls.clear()
        runner.on_order_filled(symbol="BTCUSDT", position_side="LONG")
        runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="REJECTED",
        )
        account_service.calls.clear()
        action = runner.on_order_failed(
            symbol="BTCUSDT",
            position_side="SHORT",
            status="CANCELED",
        )

        self.assertEqual(action["action"], "flatten_exposure")
        self.assertEqual(
            account_service.calls,
            [
                (
                    "close_position",
                    {
                        "symbol": "BTCUSDT",
                        "positionSide": "LONG",
                        "quantity": "0.016",
                    },
                )
            ],
        )

    def test_run_cycle_holds_when_risk_manager_requests_pause(self) -> None:
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=True,
                    should_reduce=True,
                    reason="uni_mmr_hard_limit",
                )
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        result = runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ],
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("3"),
            ),
            current_drawdown=Decimal("0.01"),
        )

        self.assertIsNone(result.selected_symbol)
        self.assertEqual(result.intent.action, "hold")

    def test_plan_profit_sweep_returns_plan_when_safe(self) -> None:
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
            ),
        )
        runner._engine.phase = StrategyPhase.HEDGED

        plan = runner.plan_profit_sweep(
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("10"),
            ),
            bucket=ProfitBucket(
                realized_pnl_total=Decimal("120"),
                realized_pnl_available_for_deposit=Decimal("120"),
                harvest_buffer=Decimal("120"),
                closed_loop_ready=True,
            ),
        )

        self.assertEqual(plan.usdt_amount, Decimal("120"))
        self.assertTrue(plan.should_subscribe_rwusd)

    def test_plan_profit_sweep_blocks_when_risk_requires_reduction(self) -> None:
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(
                    should_pause=False,
                    should_reduce=True,
                    reason="uni_mmr_soft_limit",
                )
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
            ),
        )

        plan = runner.plan_profit_sweep(
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("6"),
            ),
            bucket=ProfitBucket(
                realized_pnl_total=Decimal("120"),
                realized_pnl_available_for_deposit=Decimal("120"),
            ),
        )

        self.assertEqual(plan.usdt_amount, Decimal("0"))
        self.assertFalse(plan.should_subscribe_rwusd)

    def test_plan_profit_sweep_blocks_when_pending_rebalance_exists(self) -> None:
        transfer_planner = FakeTransferPlanner(
            SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
        )
        runner._engine.phase = StrategyPhase.HEDGED
        runner._pending_rebalance_side = PositionSide.LONG

        plan = runner.plan_profit_sweep(
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("10"),
            ),
            bucket=ProfitBucket(
                realized_pnl_total=Decimal("120"),
                realized_pnl_available_for_deposit=Decimal("120"),
                harvest_buffer=Decimal("120"),
                closed_loop_ready=True,
            ),
        )

        self.assertEqual(plan.usdt_amount, Decimal("0"))
        self.assertFalse(plan.should_subscribe_rwusd)
        self.assertEqual(plan.block_reason, "pending_rebalance")
        self.assertEqual(len(transfer_planner.sweep_calls), 0)

    def test_plan_profit_sweep_blocks_when_not_hedged(self) -> None:
        transfer_planner = FakeTransferPlanner(
            SweepPlan(usdt_amount=Decimal("120"), should_subscribe_rwusd=True)
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=FakeStateStore(),
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=transfer_planner,
        )
        runner._engine.phase = StrategyPhase.REBALANCING

        plan = runner.plan_profit_sweep(
            snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("10"),
            ),
            bucket=ProfitBucket(
                realized_pnl_total=Decimal("120"),
                realized_pnl_available_for_deposit=Decimal("120"),
                harvest_buffer=Decimal("120"),
                closed_loop_ready=True,
            ),
        )

        self.assertEqual(plan.usdt_amount, Decimal("0"))
        self.assertFalse(plan.should_subscribe_rwusd)
        self.assertEqual(plan.block_reason, "not_hedged")
        self.assertEqual(len(transfer_planner.sweep_calls), 0)

    def test_reconcile_remote_state_restores_hedged_symbol_from_remote_positions(self) -> None:
        account_service = FakeAccountService()
        account_service.remote_positions = [
            UmHedgePosition(
                symbol="ETHUSDT",
                long_qty=Decimal("0.5"),
                short_qty=Decimal("0.5"),
                long_notional=Decimal("1200"),
                short_notional=Decimal("1198"),
            )
        ]
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        action = runner.reconcile_remote_state()

        self.assertIsNone(action)
        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertEqual(state_store.state.symbol, "ETHUSDT")
        self.assertTrue(state_store.state.long_filled)
        self.assertTrue(state_store.state.short_filled)
        self.assertEqual(state_store.state.long_notional, Decimal("1200"))
        self.assertEqual(state_store.state.short_notional, Decimal("1198"))

    def test_reconcile_remote_state_restores_mixed_symbol_hedge_for_same_underlying(self) -> None:
        account_service = FakeAccountService()
        account_service.remote_positions = [
            UmHedgePosition(
                symbol="ETHUSDT",
                long_qty=Decimal("0.5"),
                short_qty=Decimal("0"),
                long_notional=Decimal("1200"),
                short_notional=Decimal("0"),
            ),
            UmHedgePosition(
                symbol="ETHUSDC",
                long_qty=Decimal("0"),
                short_qty=Decimal("0.5"),
                long_notional=Decimal("0"),
                short_notional=Decimal("1198"),
            ),
        ]
        state_store = FakeStateStore()
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "ETHUSDC", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        action = runner.reconcile_remote_state()

        self.assertIsNone(action)
        self.assertEqual(runner.symbol, "ETHUSDT")
        self.assertEqual(state_store.state.underlying_symbol, "ETH")
        self.assertEqual(state_store.state.long_symbol, "ETHUSDT")
        self.assertEqual(state_store.state.short_symbol, "ETHUSDC")
        self.assertEqual(state_store.state.long_notional, Decimal("1200"))
        self.assertEqual(state_store.state.short_notional, Decimal("1198"))

    def test_reconcile_remote_state_resets_to_idle_when_remote_has_no_position(self) -> None:
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.HEDGED,
                long_notional=Decimal("1000"),
                short_notional=Decimal("1000"),
                long_filled=True,
                short_filled=True,
            )
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()

        action = runner.reconcile_remote_state()

        self.assertIsNone(action)
        self.assertIsNone(runner.symbol)
        self.assertEqual(runner.phase, StrategyPhase.IDLE)
        self.assertIsNone(state_store.state.symbol)
        self.assertEqual(state_store.state.phase, StrategyPhase.IDLE)
        self.assertFalse(state_store.state.long_filled)
        self.assertFalse(state_store.state.short_filled)

    def test_reconcile_remote_state_returns_reconcile_required_when_remote_conflicts_with_local(self) -> None:
        account_service = FakeAccountService()
        account_service.remote_positions = [
            UmHedgePosition(
                symbol="ETHUSDT",
                long_qty=Decimal("0.5"),
                short_qty=Decimal("0.5"),
                long_notional=Decimal("1200"),
                short_notional=Decimal("1198"),
            )
        ]
        state_store = FakeStateStore(
            initial_state=HedgeState(
                symbol="BTCUSDT",
                phase=StrategyPhase.HEDGED,
                long_notional=Decimal("1000"),
                short_notional=Decimal("1000"),
                long_filled=True,
                short_filled=True,
            )
        )
        runner = LiveRunner(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )
        runner.restore_state()

        action = runner.reconcile_remote_state()

        self.assertEqual(
            action,
            {
                "action": "reconcile_required",
                "reason": "symbol_mismatch",
                "local_symbol": "BTCUSDT",
                "remote_symbol": "ETHUSDT",
            },
        )
        self.assertEqual(runner.symbol, "BTCUSDT")
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)

    def test_consume_user_stream_delegates_loop_and_updates_state(self) -> None:
        state_store = FakeStateStore()
        stream_client = FakeStreamClient(
            parsed_event=[
                {
                    "event_type": "order_filled",
                    "symbol": "BTCUSDT",
                    "position_side": "LONG",
                },
                {
                    "event_type": "order_filled",
                    "symbol": "BTCUSDT",
                    "position_side": "SHORT",
                },
            ]
        )
        runner = LiveRunner(
            account_service=FakeAccountService(),
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT"],
            target_notional=Decimal("1000"),
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
            dry_run=True,
            state_store=state_store,
            risk_manager=FakeRiskManager(
                RiskDecision(should_pause=False, should_reduce=False, reason=None)
            ),
            transfer_planner=FakeTransferPlanner(
                SweepPlan(usdt_amount=Decimal("0"), should_subscribe_rwusd=False)
            ),
        )

        runner.run_cycle(
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "close": Decimal("60000"),
                    "liquidity": Decimal("0.90"),
                    "volatility": Decimal("0.80"),
                    "funding": Decimal("0.70"),
                    "margin": Decimal("0.85"),
                    "blocked": False,
                }
            ]
        )

        consumed = runner.consume_user_stream(
            event_source=lambda _: [
                {"e": "ORDER_TRADE_UPDATE", "o": {"s": "BTCUSDT", "ps": "LONG", "X": "FILLED"}},
                {"e": "ORDER_TRADE_UPDATE", "o": {"s": "BTCUSDT", "ps": "SHORT", "X": "FILLED"}},
            ],
            keepalive_every=1,
        )

        self.assertEqual(consumed, 2)
        self.assertEqual(
            stream_client.loop_calls,
            [
                {
                    "keepalive_every": 1,
                    "max_events": None,
                    "retry_attempts": 0,
                    "retry_backoff_seconds": 1.0,
                    "retry_backoff_multiplier": 2.0,
                }
            ],
        )
        self.assertEqual(runner.phase, StrategyPhase.HEDGED)
        self.assertTrue(state_store.state.long_filled)
        self.assertTrue(state_store.state.short_filled)


if __name__ == "__main__":
    unittest.main()
