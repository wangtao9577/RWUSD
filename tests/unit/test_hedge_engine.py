"""Domain contract tests live here until the hedge engine implementation exists."""

from decimal import Decimal
import unittest

from src.domain.enums import PositionSide, StrategyPhase
from src.domain.events import ProfitTriggerEvent, RiskPauseEvent
from src.domain.models import (
    PortfolioSnapshot,
    ProfitBucket,
    SelectorSnapshot,
    StrategyPosition,
    SymbolScore,
)
from src.strategy.hedge_engine import HedgeEngine, StrategyIntent


class DomainModelTests(unittest.TestCase):
    """Covers Task 2 domain contracts without reaching into future engine behavior."""

    def test_strategy_phase_matches_task_2_contract(self) -> None:
        self.assertEqual(StrategyPhase.IDLE.value, "IDLE")
        self.assertEqual(StrategyPhase.SELECTING_SYMBOL.value, "SELECTING_SYMBOL")
        self.assertEqual(StrategyPhase.OPENING_HEDGE.value, "OPENING_HEDGE")
        self.assertEqual(StrategyPhase.HEDGED.value, "HEDGED")
        self.assertEqual(StrategyPhase.TAKING_PROFIT.value, "TAKING_PROFIT")
        self.assertEqual(StrategyPhase.REBALANCING.value, "REBALANCING")
        self.assertEqual(StrategyPhase.PROFIT_SWEEPING.value, "PROFIT_SWEEPING")
        self.assertEqual(StrategyPhase.RISK_REDUCTION.value, "RISK_REDUCTION")
        self.assertEqual(StrategyPhase.PAUSED.value, "PAUSED")

    def test_domain_models_have_safe_defaults(self) -> None:
        position = StrategyPosition(symbol="BTCUSDT")
        snapshot = PortfolioSnapshot(account_equity=Decimal("1000"))
        bucket = ProfitBucket()

        self.assertEqual(position.phase, StrategyPhase.IDLE)
        self.assertEqual(position.long_qty, Decimal("0"))
        self.assertEqual(position.long_entry_price, Decimal("0"))
        self.assertEqual(position.short_unrealized_pnl, Decimal("0"))
        self.assertEqual(snapshot.uni_mmr, Decimal("0"))
        self.assertEqual(snapshot.total_abs_notional, Decimal("0"))
        self.assertEqual(snapshot.spot_rwusd_balance, Decimal("0"))
        self.assertEqual(bucket.realized_pnl_total, Decimal("0"))
        self.assertEqual(bucket.rwusd_principal, Decimal("0"))

    def test_selector_snapshot_uses_scores_contract(self) -> None:
        score = SymbolScore(
            symbol="BTCUSDT",
            score=Decimal("1.25"),
            liquidity_score=Decimal("0.90"),
            volatility_score=Decimal("0.60"),
            funding_score=Decimal("0.10"),
            margin_efficiency_score=Decimal("0.80"),
        )
        snapshot = SelectorSnapshot(
            scores=[score],
            selected_symbol="BTCUSDT",
            cooldown_symbol="ETHUSDT",
        )

        self.assertEqual(snapshot.selected_symbol, "BTCUSDT")
        self.assertEqual(snapshot.cooldown_symbol, "ETHUSDT")
        self.assertEqual(snapshot.scores[0].score, Decimal("1.25"))
        self.assertIsNone(snapshot.scores[0].reject_reason)

    def test_selector_snapshot_defaults_to_empty_scores(self) -> None:
        snapshot = SelectorSnapshot()

        self.assertEqual(snapshot.scores, [])

    def test_profit_trigger_event_keeps_payload(self) -> None:
        event = ProfitTriggerEvent(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            unrealized_pnl=Decimal("12.5"),
        )

        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.side, PositionSide.LONG)
        self.assertEqual(event.unrealized_pnl, Decimal("12.5"))

    def test_risk_pause_event_keeps_payload(self) -> None:
        event = RiskPauseEvent(reason="uni mmr threshold", uni_mmr=Decimal("8.4"))

        self.assertEqual(event.reason, "uni mmr threshold")
        self.assertEqual(event.uni_mmr, Decimal("8.4"))


class HedgeEngineStateMachineTests(unittest.TestCase):
    def test_on_symbol_selected_returns_open_hedge_intent_and_advances_phase(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )

        intent = engine.on_symbol_selected("BTCUSDT")

        self.assertEqual(intent, StrategyIntent.open_hedge("BTCUSDT", Decimal("250"), Decimal("250")))
        self.assertEqual(engine.phase, StrategyPhase.OPENING_HEDGE)

    def test_mark_hedged_moves_phase_to_hedged(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )

        engine.mark_hedged("BTCUSDT")

        self.assertEqual(engine.phase, StrategyPhase.HEDGED)

    def test_on_pnl_update_returns_take_profit_when_long_reaches_threshold(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )
        engine.on_symbol_selected("BTCUSDT")
        engine.mark_hedged("BTCUSDT")

        intent = engine.on_pnl_update(
            long_unrealized=Decimal("15"),
            short_unrealized=Decimal("3"),
        )

        self.assertEqual(
            intent,
            StrategyIntent.take_profit("BTCUSDT", PositionSide.LONG, Decimal("15")),
        )
        self.assertEqual(engine.phase, StrategyPhase.TAKING_PROFIT)

    def test_on_pnl_update_returns_hold_when_thresholds_not_reached(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )
        engine.on_symbol_selected("BTCUSDT")
        engine.mark_hedged("BTCUSDT")

        intent = engine.on_pnl_update(
            long_unrealized=Decimal("14.99"),
            short_unrealized=Decimal("9.99"),
        )

        self.assertEqual(intent, StrategyIntent.hold())
        self.assertEqual(engine.phase, StrategyPhase.HEDGED)

    def test_on_pnl_update_returns_take_profit_when_short_reaches_threshold(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )
        engine.on_symbol_selected("BTCUSDT")
        engine.mark_hedged("BTCUSDT")

        intent = engine.on_pnl_update(
            long_unrealized=Decimal("5"),
            short_unrealized=Decimal("10"),
        )

        self.assertEqual(
            intent,
            StrategyIntent.take_profit("BTCUSDT", PositionSide.SHORT, Decimal("10")),
        )
        self.assertEqual(engine.phase, StrategyPhase.TAKING_PROFIT)

    def test_take_profit_event_moves_engine_to_rebalancing_after_profit_is_recorded(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )
        engine.on_symbol_selected("BTCUSDT")
        engine.mark_hedged("BTCUSDT")
        take_profit_intent = engine.on_pnl_update(
            long_unrealized=Decimal("20"),
            short_unrealized=Decimal("-8"),
        )

        rebalance_intent = engine.on_take_profit_completed(
            symbol="BTCUSDT",
            closed_side=PositionSide.LONG,
        )

        self.assertEqual(take_profit_intent.action, "take_profit")
        self.assertEqual(rebalance_intent.action, "rebalance")
        self.assertEqual(rebalance_intent.symbol, "BTCUSDT")
        self.assertEqual(rebalance_intent.side, PositionSide.LONG)
        self.assertEqual(engine.phase, StrategyPhase.REBALANCING)

    def test_restore_completion_returns_engine_to_hedged(self) -> None:
        engine = HedgeEngine(
            target_notional=Decimal("250"),
            long_take_profit=Decimal("15"),
            short_take_profit=Decimal("10"),
        )
        engine.on_symbol_selected("BTCUSDT")
        engine.mark_hedged("BTCUSDT")
        engine.on_pnl_update(
            long_unrealized=Decimal("20"),
            short_unrealized=Decimal("-8"),
        )
        engine.on_take_profit_completed(
            symbol="BTCUSDT",
            closed_side=PositionSide.LONG,
        )

        engine.on_rebalance_restored("BTCUSDT")

        self.assertEqual(engine.phase, StrategyPhase.HEDGED)


if __name__ == "__main__":
    unittest.main()
