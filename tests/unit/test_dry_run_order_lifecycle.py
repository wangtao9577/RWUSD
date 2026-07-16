import unittest
from decimal import Decimal

from src.domain.enums import PositionSide
from src.strategy.dry_run_order_lifecycle import (
    DryRunMatchingEngine,
    DryRunOrderLifecycle,
    DryRunQuoteRuntime,
)
from src.strategy.tick_quote_planner import QuoteOrder, TickQuotePlan, TickQuotePlanner


def opening_quote(position_side: PositionSide, quantity: str) -> QuoteOrder:
    return QuoteOrder(
        position_side=position_side,
        side="BUY" if position_side == PositionSide.LONG else "SELL",
        quantity=Decimal(quantity),
        price=Decimal("1800"),
        reduce_only=False,
    )


class DryRunOrderLifecycleTests(unittest.TestCase):
    def test_requote_cancels_active_orders_before_creating_replacements(self) -> None:
        lifecycle = DryRunOrderLifecycle()
        first_update = lifecycle.replace_orders(
            TickQuotePlan(
                cancel_open_orders=True,
                orders=(
                    opening_quote(PositionSide.LONG, "2"),
                    opening_quote(PositionSide.SHORT, "2"),
                ),
                reason="open_both",
            )
        )
        second_update = lifecycle.replace_orders(
            TickQuotePlan(
                cancel_open_orders=True,
                orders=(opening_quote(PositionSide.LONG, "1"),),
                reason="restore_long",
            )
        )

        self.assertEqual(first_update.created_order_ids, ("dry-1", "dry-2"))
        self.assertEqual(second_update.canceled_order_ids, ("dry-1", "dry-2"))
        self.assertEqual(second_update.created_order_ids, ("dry-3",))
        self.assertEqual(lifecycle.get_order("dry-1").status, "CANCELED")
        self.assertEqual([order.order_id for order in lifecycle.active_orders], ["dry-3"])

    def test_cumulative_partial_fills_are_idempotent_and_update_position(self) -> None:
        lifecycle = DryRunOrderLifecycle()
        order_id = lifecycle.replace_orders(
            TickQuotePlan(True, (opening_quote(PositionSide.LONG, "5"),), "open_long")
        ).created_order_ids[0]

        partial = lifecycle.apply_execution(
            order_id=order_id,
            cumulative_filled_quantity=Decimal("2"),
            status="PARTIALLY_FILLED",
        )
        duplicate = lifecycle.apply_execution(
            order_id=order_id,
            cumulative_filled_quantity=Decimal("2"),
            status="PARTIALLY_FILLED",
        )
        completed = lifecycle.apply_execution(
            order_id=order_id,
            cumulative_filled_quantity=Decimal("5"),
            status="FILLED",
        )

        self.assertEqual(partial.status, "PARTIALLY_FILLED")
        self.assertEqual(duplicate.cumulative_filled_quantity, Decimal("2"))
        self.assertEqual(completed.status, "FILLED")
        self.assertEqual(lifecycle.long_quantity, Decimal("5"))
        self.assertEqual(lifecycle.active_orders, ())

    def test_expired_orders_cannot_receive_late_fills(self) -> None:
        lifecycle = DryRunOrderLifecycle()
        order_id = lifecycle.replace_orders(
            TickQuotePlan(True, (opening_quote(PositionSide.SHORT, "2"),), "open_short")
        ).created_order_ids[0]

        expired = lifecycle.expire_order(order_id)

        self.assertEqual(expired.status, "EXPIRED")
        with self.assertRaisesRegex(ValueError, "order_not_active"):
            lifecycle.apply_execution(
                order_id=order_id,
                cumulative_filled_quantity=Decimal("1"),
                status="PARTIALLY_FILLED",
            )

    def test_risk_reduction_replaces_orders_and_decrements_both_positions(self) -> None:
        lifecycle = DryRunOrderLifecycle(
            long_quantity=Decimal("3"),
            short_quantity=Decimal("2"),
        )
        lifecycle.replace_orders(
            TickQuotePlan(True, (opening_quote(PositionSide.LONG, "1"),), "stale_open")
        )

        update = lifecycle.replace_with_risk_reduction(mid_price=Decimal("1750"))
        long_order_id, short_order_id = update.created_order_ids
        long_order = lifecycle.get_order(long_order_id)
        short_order = lifecycle.get_order(short_order_id)
        lifecycle.apply_execution(
            order_id=long_order_id,
            cumulative_filled_quantity=Decimal("3"),
            status="FILLED",
        )
        lifecycle.apply_execution(
            order_id=short_order_id,
            cumulative_filled_quantity=Decimal("2"),
            status="FILLED",
        )

        self.assertEqual(update.canceled_order_ids, ("dry-1",))
        self.assertTrue(long_order.reduce_only)
        self.assertTrue(short_order.reduce_only)
        self.assertEqual((long_order.side, short_order.side), ("SELL", "BUY"))
        self.assertEqual(lifecycle.long_quantity, Decimal("0"))
        self.assertEqual(lifecycle.short_quantity, Decimal("0"))

    def test_rejects_reduce_only_fill_larger_than_position(self) -> None:
        lifecycle = DryRunOrderLifecycle(long_quantity=Decimal("1"))
        order_id = lifecycle.replace_with_risk_reduction(
            mid_price=Decimal("1800")
        ).created_order_ids[0]

        with self.assertRaisesRegex(ValueError, "cumulative_fill_exceeds_order_quantity"):
            lifecycle.apply_execution(
                order_id=order_id,
                cumulative_filled_quantity=Decimal("2"),
                status="FILLED",
            )

    def test_quote_runtime_requotes_from_filled_inventory_and_closes_on_hard_risk(self) -> None:
        runtime = DryRunQuoteRuntime(
            TickQuotePlanner(target_quantity=Decimal("2"))
        )

        opening = runtime.on_opening_tick(Decimal("1800"))
        long_order_id, short_order_id = opening.lifecycle_update.created_order_ids
        runtime.lifecycle.apply_execution(
            order_id=long_order_id,
            cumulative_filled_quantity=Decimal("2"),
            status="FILLED",
        )
        runtime.lifecycle.apply_execution(
            order_id=short_order_id,
            cumulative_filled_quantity=Decimal("2"),
            status="FILLED",
        )
        target_reached = runtime.on_opening_tick(Decimal("1801"))
        risk = runtime.on_hard_risk_tick(Decimal("1795"))

        self.assertEqual(opening.plan.reason, "balanced_below_target_open_both")
        self.assertEqual(target_reached.plan.reason, "target_position_reached")
        self.assertEqual(risk.plan.reason, "risk_reduce_both")
        self.assertEqual(len(risk.lifecycle_update.created_order_ids), 2)

    def test_matching_engine_emits_partial_then_completed_fill_for_marketable_order(self) -> None:
        lifecycle = DryRunOrderLifecycle()
        order_id = lifecycle.replace_orders(
            TickQuotePlan(True, (opening_quote(PositionSide.LONG, "4"),), "open_long")
        ).created_order_ids[0]
        matcher = DryRunMatchingEngine(
            fill_fraction=Decimal("0.5"),
            min_fill_quantity=Decimal("1"),
        )

        first = matcher.match(lifecycle, Decimal("1800"))
        second = matcher.match(lifecycle, Decimal("1800"))
        third = matcher.match(lifecycle, Decimal("1800"))

        self.assertEqual(first[0].status, "PARTIALLY_FILLED")
        self.assertEqual(second[0].cumulative_filled_quantity, Decimal("3"))
        self.assertEqual(third[0].status, "FILLED")
        self.assertEqual(lifecycle.get_order(order_id).cumulative_filled_quantity, Decimal("4"))

    def test_timeout_expiry_and_requote_counters_round_trip_in_snapshot(self) -> None:
        lifecycle = DryRunOrderLifecycle()
        order_id = lifecycle.replace_orders(
            TickQuotePlan(True, (opening_quote(PositionSide.LONG, "2"),), "open_long")
        ).created_order_ids[0]
        lifecycle.advance_cycle()
        expired = lifecycle.expire_orders_older_than(1)
        lifecycle.register_timeout_requote()
        restored = DryRunOrderLifecycle.from_snapshot(lifecycle.to_snapshot())

        self.assertEqual(expired[0].order_id, order_id)
        self.assertEqual(restored.get_order(order_id).status, "EXPIRED")
        self.assertEqual(restored.expired_order_count, 1)
        self.assertEqual(restored.timeout_requote_count, 1)


if __name__ == "__main__":
    unittest.main()
