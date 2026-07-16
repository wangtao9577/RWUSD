"""Minimal profit-threshold boundary for Task 5 strategy decisions."""

from decimal import Decimal
from src.domain.models import ProfitBucket


class PnlManager:
    """Evaluates whether long or short hedge legs reached their profit targets."""

    def __init__(self, long_take_profit: Decimal, short_take_profit: Decimal) -> None:
        self._long_take_profit = long_take_profit
        self._short_take_profit = short_take_profit

    def should_take_profit(
        self,
        long_unrealized: Decimal,
        short_unrealized: Decimal,
    ) -> bool:
        return (
            long_unrealized >= self._long_take_profit
            or short_unrealized >= self._short_take_profit
        )

    def record_take_profit(
        self,
        bucket: ProfitBucket,
        realized_pnl: Decimal,
    ) -> ProfitBucket:
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
