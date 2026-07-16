from decimal import Decimal
import unittest

from src.domain.models import PortfolioSnapshot, ProfitBucket
from src.portfolio.transfers import RedeemPlan, SweepPlan, TransferPlanner


def ready_bucket(**kwargs) -> ProfitBucket:
    kwargs.setdefault(
        "harvest_buffer",
        kwargs.get("realized_pnl_available_for_deposit", Decimal("0")),
    )
    return ProfitBucket(closed_loop_ready=True, **kwargs)


class TransferPlannerTests(unittest.TestCase):
    def test_plan_sweep_returns_rwusd_subscription_amount(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("430"),
        )
        bucket = ready_bucket(
            realized_pnl_total=Decimal("120"),
            realized_pnl_available_for_deposit=Decimal("80"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("80"),
                should_subscribe_rwusd=True,
            ),
        )

    def test_plan_sweep_blocks_when_amount_is_below_minimum(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("320"),
        )
        bucket = ready_bucket(
            realized_pnl_total=Decimal("40"),
            realized_pnl_available_for_deposit=Decimal("20"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="below_min_sweep",
            ),
        )

    def test_plan_sweep_blocks_when_available_balance_equals_pm_reserve(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("300"),
        )
        bucket = ready_bucket(
            realized_pnl_available_for_deposit=Decimal("80"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="pm_reserve_locked",
            ),
        )

    def test_plan_sweep_allows_amount_equal_to_minimum(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("325"),
        )
        bucket = ready_bucket(
            realized_pnl_available_for_deposit=Decimal("25"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("25"),
                should_subscribe_rwusd=True,
            ),
        )

    def test_plan_sweep_caps_amount_by_max_transferable_balance(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("360"),
        )
        bucket = ready_bucket(
            realized_pnl_available_for_deposit=Decimal("90"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("60"),
                should_subscribe_rwusd=True,
            ),
        )

    def test_plan_sweep_blocks_until_closed_loop_is_ready(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("430"),
        )
        bucket = ProfitBucket(
            realized_pnl_available_for_deposit=Decimal("80"),
            harvest_buffer=Decimal("80"),
        )

        plan = planner.plan_sweep(snapshot, bucket)

        self.assertEqual(
            plan,
            SweepPlan(
                usdt_amount=Decimal("0"),
                should_subscribe_rwusd=False,
                block_reason="closed_loop_not_ready",
            ),
        )

    def test_plan_redeem_returns_amount_when_unimmr_is_below_redeem_line(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("50"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("210"),
            uni_mmr=Decimal("5.5"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("120"),
            rwusd_redeemable=Decimal("120"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("90.91"),
                should_redeem_rwusd=True,
            ),
        )

    def test_plan_redeem_uses_larger_unimmr_gap_when_it_exceeds_reserve_gap(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("10"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("290"),
            uni_mmr=Decimal("5.5"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("200"),
            rwusd_redeemable=Decimal("200"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("90.91"),
                should_redeem_rwusd=True,
            ),
        )

    def test_plan_redeem_caps_amount_by_rwusd_redeemable(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("50"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("220"),
            uni_mmr=Decimal("5.5"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("120"),
            rwusd_redeemable=Decimal("70"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("70"),
                should_redeem_rwusd=True,
            ),
        )

    def test_plan_redeem_blocks_when_redeemable_is_below_minimum(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("50"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("260"),
            uni_mmr=Decimal("5.5"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("40"),
            rwusd_redeemable=Decimal("40"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("0"),
                should_redeem_rwusd=False,
            ),
        )

    def test_plan_redeem_blocks_when_unimmr_recovers_above_redeem_line(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("50"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("320"),
            uni_mmr=Decimal("6.1"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("120"),
            rwusd_redeemable=Decimal("120"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("0"),
                should_redeem_rwusd=False,
            ),
        )

    def test_plan_redeem_returns_amount_when_available_balance_is_below_pm_reserve_even_if_unimmr_is_safe(self) -> None:
        planner = TransferPlanner(
            min_sweep=Decimal("25"),
            pm_reserve=Decimal("300"),
            min_redeem=Decimal("10"),
            redeem_unimmr=Decimal("6"),
        )
        snapshot = PortfolioSnapshot(
            account_equity=Decimal("1000"),
            available_balance=Decimal("260"),
            uni_mmr=Decimal("12"),
        )
        bucket = ProfitBucket(
            rwusd_principal=Decimal("120"),
            rwusd_redeemable=Decimal("120"),
        )

        plan = planner.plan_redeem(snapshot=snapshot, bucket=bucket)

        self.assertEqual(
            plan,
            RedeemPlan(
                usdt_amount=Decimal("40"),
                should_redeem_rwusd=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
