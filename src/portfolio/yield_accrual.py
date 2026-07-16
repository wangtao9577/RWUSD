from decimal import Decimal

from src.domain.models import ProfitBucket


def accrue_rwusd_interest(
    bucket: ProfitBucket,
    apr: Decimal,
    elapsed_hours: Decimal,
) -> ProfitBucket:
    hourly_rate = apr / Decimal("365") / Decimal("24")
    accrued = bucket.rwusd_principal * hourly_rate * elapsed_hours
    return ProfitBucket(
        realized_pnl_total=bucket.realized_pnl_total,
        realized_pnl_available_for_deposit=bucket.realized_pnl_available_for_deposit,
        harvest_buffer=bucket.harvest_buffer,
        rwusd_principal=bucket.rwusd_principal,
        rwusd_interest_accrued=bucket.rwusd_interest_accrued + accrued,
        rwusd_redeemable=bucket.rwusd_redeemable,
        harvest_count=bucket.harvest_count,
        deposit_count=bucket.deposit_count,
        redeem_count=bucket.redeem_count,
        closed_loop_ready=bucket.closed_loop_ready,
        last_rebalance_action=bucket.last_rebalance_action,
        sweep_block_reason=bucket.sweep_block_reason,
    )
