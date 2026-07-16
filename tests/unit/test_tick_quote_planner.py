import unittest
from decimal import Decimal

from src.domain.enums import PositionSide
from src.strategy.tick_quote_planner import TickQuotePlanner


class TickQuotePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = TickQuotePlanner(target_quantity=Decimal("5"))

    def test_opening_holds_after_both_legs_reach_target(self) -> None:
        plan = self.planner.plan_opening(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("5"),
            short_quantity=Decimal("5"),
        )

        self.assertTrue(plan.cancel_open_orders)
        self.assertEqual(plan.orders, ())
        self.assertEqual(plan.reason, "target_position_reached")

    def test_opening_places_both_legs_when_balanced_below_target(self) -> None:
        plan = self.planner.plan_opening(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("3"),
            short_quantity=Decimal("3"),
        )

        self.assertEqual(plan.reason, "balanced_below_target_open_both")
        self.assertEqual(
            [(order.position_side, order.side, order.quantity, order.reduce_only) for order in plan.orders],
            [
                (PositionSide.LONG, "BUY", Decimal("2"), False),
                (PositionSide.SHORT, "SELL", Decimal("2"), False),
            ],
        )

    def test_opening_only_fills_the_missing_leg_when_quantities_differ(self) -> None:
        plan = self.planner.plan_opening(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("3"),
            short_quantity=Decimal("4"),
        )

        self.assertEqual(plan.reason, "unbalanced_open_missing_leg")
        self.assertEqual(len(plan.orders), 1)
        order = plan.orders[0]
        self.assertEqual(order.position_side, PositionSide.LONG)
        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.quantity, Decimal("1"))
        self.assertFalse(order.reduce_only)

    def test_opening_trims_an_excess_leg_and_fills_the_deficit(self) -> None:
        plan = self.planner.plan_opening(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("6"),
            short_quantity=Decimal("4"),
        )

        self.assertEqual(plan.reason, "trim_excess_and_fill_deficit")
        self.assertEqual(
            [(order.position_side, order.side, order.quantity, order.reduce_only) for order in plan.orders],
            [
                (PositionSide.LONG, "SELL", Decimal("1"), True),
                (PositionSide.SHORT, "SELL", Decimal("1"), False),
            ],
        )

    def test_exit_reduces_both_legs_when_balanced_and_pnl_is_unrealized(self) -> None:
        plan = self.planner.plan_exit(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("3"),
            short_quantity=Decimal("3"),
            all_unrealized_pnl_realized=False,
        )

        self.assertEqual(plan.reason, "balanced_reduce_both")
        self.assertEqual(
            [(order.position_side, order.side, order.quantity, order.reduce_only) for order in plan.orders],
            [
                (PositionSide.LONG, "SELL", Decimal("3"), True),
                (PositionSide.SHORT, "BUY", Decimal("3"), True),
            ],
        )

    def test_exit_restores_only_the_missing_leg_when_unbalanced(self) -> None:
        plan = self.planner.plan_exit(
            mid_price=Decimal("1800"),
            long_quantity=Decimal("3"),
            short_quantity=Decimal("5"),
            all_unrealized_pnl_realized=False,
        )

        self.assertEqual(plan.reason, "unbalanced_restore_missing_leg")
        self.assertEqual(len(plan.orders), 1)
        self.assertEqual(plan.orders[0].position_side, PositionSide.LONG)
        self.assertEqual(plan.orders[0].quantity, Decimal("2"))

    def test_rejects_invalid_price_and_quantity(self) -> None:
        with self.assertRaisesRegex(ValueError, "mid_price_must_be_positive"):
            self.planner.plan_opening(
                mid_price=Decimal("0"),
                long_quantity=Decimal("0"),
                short_quantity=Decimal("0"),
            )

        with self.assertRaisesRegex(ValueError, "position_quantity_must_not_be_negative"):
            self.planner.plan_opening(
                mid_price=Decimal("1"),
                long_quantity=Decimal("-1"),
                short_quantity=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
