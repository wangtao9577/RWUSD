from decimal import Decimal
import unittest

from src.domain.enums import PositionSide
from src.domain.events import HarvestExecutedEvent


def _import_harvest_types():
    try:
        from src.strategy.harvest_rules import HarvestDecision, HarvestRule
    except ModuleNotFoundError as exc:
        raise AssertionError("src.strategy.harvest_rules module is missing") from exc

    return HarvestDecision, HarvestRule


class HarvestRuleTests(unittest.TestCase):
    def test_harvest_executed_event_tracks_gross_and_net_pnl_fields(self) -> None:
        event = HarvestExecutedEvent(
            symbol="BTCUSDT",
            side=PositionSide.SHORT,
            gross_pnl=Decimal("42"),
            net_pnl=Decimal("38.5"),
            estimated_cost=Decimal("3.5"),
        )

        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.side, PositionSide.SHORT)
        self.assertEqual(event.gross_pnl, Decimal("42"))
        self.assertEqual(event.net_pnl, Decimal("38.5"))
        self.assertEqual(event.estimated_cost, Decimal("3.5"))

    def test_returns_should_harvest_when_net_pnl_stays_above_threshold_after_costs(self) -> None:
        HarvestDecision, HarvestRule = _import_harvest_types()
        rule = HarvestRule(
            taker_fee_bps=Decimal("5"),
            slippage_bps=Decimal("3"),
            min_net_pnl=Decimal("20"),
        )

        decision = rule.evaluate(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            unrealized_pnl=Decimal("30"),
            target_notional=Decimal("1000"),
            min_safe_unimmr=Decimal("12"),
            uni_mmr=Decimal("15"),
            recent_funding_cost=Decimal("2"),
        )

        self.assertEqual(
            decision,
            HarvestDecision(
                should_harvest=True,
                net_pnl=Decimal("26.4"),
                gross_pnl=Decimal("30"),
                estimated_cost=Decimal("3.6"),
                reason=None,
            ),
        )

    def test_returns_threshold_reason_when_costs_consume_profit(self) -> None:
        HarvestDecision, HarvestRule = _import_harvest_types()
        rule = HarvestRule(
            taker_fee_bps=Decimal("5"),
            slippage_bps=Decimal("3"),
            min_net_pnl=Decimal("1"),
        )

        decision = rule.evaluate(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            unrealized_pnl=Decimal("3"),
            target_notional=Decimal("1000"),
            min_safe_unimmr=Decimal("12"),
            uni_mmr=Decimal("15"),
            recent_funding_cost=Decimal("2"),
        )

        self.assertFalse(decision.should_harvest)
        self.assertEqual(decision.net_pnl, Decimal("-0.6"))
        self.assertEqual(decision.gross_pnl, Decimal("3"))
        self.assertEqual(decision.estimated_cost, Decimal("3.6"))
        self.assertEqual(decision.reason, "net_profit_below_threshold")

    def test_rejects_harvest_when_uni_mmr_is_below_safe_floor(self) -> None:
        HarvestDecision, HarvestRule = _import_harvest_types()
        rule = HarvestRule(
            taker_fee_bps=Decimal("5"),
            slippage_bps=Decimal("3"),
            min_net_pnl=Decimal("20"),
        )

        decision = rule.evaluate(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            unrealized_pnl=Decimal("30"),
            target_notional=Decimal("1000"),
            min_safe_unimmr=Decimal("12"),
            uni_mmr=Decimal("11.9"),
            recent_funding_cost=Decimal("2"),
        )

        self.assertEqual(
            decision,
            HarvestDecision(
                should_harvest=False,
                net_pnl=Decimal("26.4"),
                gross_pnl=Decimal("30"),
                estimated_cost=Decimal("3.6"),
                reason="unimmr_below_harvest_floor",
            ),
        )


if __name__ == "__main__":
    unittest.main()
