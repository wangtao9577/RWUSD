from decimal import Decimal
import unittest

from src.strategy.position_sizing import OrderSizingRule, normalize_order_quantity


class PositionSizingTests(unittest.TestCase):
    def test_normalize_order_quantity_rounds_down_to_step_size(self) -> None:
        rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )

        quantity = normalize_order_quantity(
            target_notional=Decimal("1000"),
            price=Decimal("60000"),
            rule=rule,
        )

        self.assertEqual(quantity, Decimal("0.016"))

    def test_normalize_order_quantity_returns_none_when_below_min_qty(self) -> None:
        rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.050"),
            min_notional=Decimal("5"),
        )

        quantity = normalize_order_quantity(
            target_notional=Decimal("1000"),
            price=Decimal("60000"),
            rule=rule,
        )

        self.assertIsNone(quantity)

    def test_normalize_order_quantity_returns_none_when_below_min_notional(self) -> None:
        rule = OrderSizingRule(
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("2000"),
        )

        quantity = normalize_order_quantity(
            target_notional=Decimal("1000"),
            price=Decimal("60000"),
            rule=rule,
        )

        self.assertIsNone(quantity)


if __name__ == "__main__":
    unittest.main()
