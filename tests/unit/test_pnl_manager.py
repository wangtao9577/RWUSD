from decimal import Decimal
import unittest

from src.domain.models import PortfolioSnapshot, ProfitBucket
from src.portfolio.transfers import TransferPlanner
from src.portfolio.yield_accrual import accrue_rwusd_interest
from src.strategy.pnl_manager import PnlManager


class PnlManagerTests(unittest.TestCase):
    def test_profit_bucket_accumulates_realized_take_profit(self) -> None:
        manager = PnlManager(
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
        )
        bucket = ProfitBucket()

        updated = manager.record_take_profit(
            bucket=bucket,
            realized_pnl=Decimal("35"),
        )

        self.assertEqual(updated.realized_pnl_total, Decimal("35"))
        self.assertEqual(updated.realized_pnl_available_for_deposit, Decimal("35"))
        self.assertEqual(updated.harvest_buffer, Decimal("35"))
        self.assertEqual(updated.harvest_count, 1)
        self.assertFalse(updated.closed_loop_ready)
        self.assertEqual(updated.last_rebalance_action, "take_profit")
        self.assertEqual(updated.sweep_block_reason, "pending_rebalance")

    def test_profit_bucket_consumes_available_profit_after_sweep(self) -> None:
        manager = PnlManager(
            long_take_profit=Decimal("25"),
            short_take_profit=Decimal("25"),
        )
        bucket = ProfitBucket(
            realized_pnl_total=Decimal("80"),
            realized_pnl_available_for_deposit=Decimal("80"),
            harvest_buffer=Decimal("80"),
            rwusd_principal=Decimal("10"),
            rwusd_interest_accrued=Decimal("5.5"),
            rwusd_redeemable=Decimal("6"),
            harvest_count=2,
            deposit_count=3,
            redeem_count=4,
            closed_loop_ready=True,
            last_rebalance_action="take_profit",
        )

        updated = manager.record_sweep(
            bucket=bucket,
            sweep_amount=Decimal("50"),
        )

        self.assertEqual(updated.realized_pnl_total, Decimal("80"))
        self.assertEqual(updated.realized_pnl_available_for_deposit, Decimal("30"))
        self.assertEqual(updated.harvest_buffer, Decimal("30"))
        self.assertEqual(updated.rwusd_principal, Decimal("60"))
        self.assertEqual(updated.rwusd_interest_accrued, Decimal("5.5"))
        self.assertEqual(updated.rwusd_redeemable, Decimal("56"))
        self.assertEqual(updated.harvest_count, 2)
        self.assertEqual(updated.deposit_count, 4)
        self.assertEqual(updated.redeem_count, 4)
        self.assertTrue(updated.closed_loop_ready)
        self.assertEqual(updated.last_rebalance_action, "sweep")
        self.assertIsNone(updated.sweep_block_reason)

    def test_plan_sweep_uses_harvest_buffer_and_reports_block_reason(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("100"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("160"),
        )
        bucket = ProfitBucket(
            realized_pnl_total=Decimal("40"),
            realized_pnl_available_for_deposit=Decimal("0"),
            harvest_buffer=Decimal("40"),
            closed_loop_ready=True,
        )

        plan = planner.plan_sweep(snapshot=snapshot, bucket=bucket)

        self.assertTrue(plan.should_subscribe_rwusd)
        self.assertEqual(plan.usdt_amount, Decimal("40"))
        self.assertIsNone(plan.block_reason)

    def test_plan_sweep_blocks_when_closed_loop_not_ready(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("100"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("160"),
        )
        bucket = ProfitBucket(
            realized_pnl_total=Decimal("40"),
            realized_pnl_available_for_deposit=Decimal("40"),
            harvest_buffer=Decimal("40"),
            closed_loop_ready=False,
            sweep_block_reason="awaiting_rebalance",
        )

        plan = planner.plan_sweep(snapshot=snapshot, bucket=bucket)

        self.assertFalse(plan.should_subscribe_rwusd)
        self.assertEqual(plan.usdt_amount, Decimal("0"))
        self.assertEqual(plan.block_reason, "awaiting_rebalance")

    def test_accrue_rwusd_interest_preserves_closed_loop_fields(self) -> None:
        bucket = ProfitBucket(
            realized_pnl_total=Decimal("40"),
            realized_pnl_available_for_deposit=Decimal("10"),
            harvest_buffer=Decimal("8"),
            rwusd_principal=Decimal("100"),
            rwusd_interest_accrued=Decimal("2"),
            rwusd_redeemable=Decimal("102"),
            harvest_count=1,
            deposit_count=2,
            redeem_count=3,
            closed_loop_ready=True,
            last_rebalance_action="sweep",
            sweep_block_reason="none",
        )

        updated = accrue_rwusd_interest(
            bucket=bucket,
            apr=Decimal("0.365"),
            elapsed_hours=Decimal("24"),
        )

        self.assertEqual(updated.harvest_buffer, Decimal("8"))
        self.assertTrue(updated.closed_loop_ready)
        self.assertEqual(updated.last_rebalance_action, "sweep")
        self.assertEqual(updated.sweep_block_reason, "none")


if __name__ == "__main__":
    unittest.main()
