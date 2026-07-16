from dataclasses import dataclass
from decimal import Decimal, ROUND_UP

from src.domain.models import PortfolioSnapshot, ProfitBucket


ZERO = Decimal("0")


@dataclass(slots=True)
class SweepPlan:
    usdt_amount: Decimal
    should_subscribe_rwusd: bool
    block_reason: str | None = None


@dataclass(slots=True)
class RedeemPlan:
    usdt_amount: Decimal
    should_redeem_rwusd: bool


class TransferPlanner:
    def __init__(
        self,
        min_sweep: Decimal,
        pm_reserve: Decimal,
        min_redeem: Decimal = ZERO,
        redeem_unimmr: Decimal = ZERO,
    ) -> None:
        self._min_sweep = min_sweep
        self._pm_reserve = pm_reserve
        self._min_redeem = min_redeem
        self._redeem_unimmr = redeem_unimmr

    def _required_redeem_for_unimmr(
        self,
        snapshot: PortfolioSnapshot,
        bucket: ProfitBucket,
    ) -> Decimal:
        if snapshot.uni_mmr >= self._redeem_unimmr:
            return ZERO
        if snapshot.uni_mmr <= ZERO or snapshot.account_equity <= ZERO:
            return bucket.rwusd_redeemable

        topup = snapshot.account_equity * (
            (self._redeem_unimmr / snapshot.uni_mmr) - Decimal("1")
        )
        if topup <= ZERO:
            return ZERO
        return topup.quantize(Decimal("0.01"), rounding=ROUND_UP)

    def plan_sweep(
        self,
        snapshot: PortfolioSnapshot,
        bucket: ProfitBucket,
    ) -> SweepPlan:
        if not bucket.closed_loop_ready:
            return SweepPlan(
                usdt_amount=ZERO,
                should_subscribe_rwusd=False,
                block_reason=bucket.sweep_block_reason or "closed_loop_not_ready",
            )

        if snapshot.available_balance <= self._pm_reserve:
            return SweepPlan(
                usdt_amount=ZERO,
                should_subscribe_rwusd=False,
                block_reason="pm_reserve_locked",
            )

        max_transferable = snapshot.available_balance - self._pm_reserve
        sweep_amount = min(
            bucket.harvest_buffer,
            max_transferable,
        )
        if sweep_amount < self._min_sweep:
            return SweepPlan(
                usdt_amount=ZERO,
                should_subscribe_rwusd=False,
                block_reason="below_min_sweep",
            )

        return SweepPlan(
            usdt_amount=sweep_amount,
            should_subscribe_rwusd=True,
            block_reason=None,
        )

    def plan_redeem(
        self,
        snapshot: PortfolioSnapshot,
        bucket: ProfitBucket,
    ) -> RedeemPlan:
        reserve_gap = max(ZERO, self._pm_reserve - snapshot.available_balance)
        unimmr_gap = self._required_redeem_for_unimmr(snapshot=snapshot, bucket=bucket)
        uni_mmr_breach = unimmr_gap > ZERO
        if not uni_mmr_breach and reserve_gap <= ZERO:
            return RedeemPlan(usdt_amount=ZERO, should_redeem_rwusd=False)

        requested_amount = max(reserve_gap, unimmr_gap)
        amount = min(requested_amount, bucket.rwusd_redeemable)
        if amount < self._min_redeem:
            return RedeemPlan(usdt_amount=ZERO, should_redeem_rwusd=False)

        return RedeemPlan(
            usdt_amount=amount,
            should_redeem_rwusd=True,
        )
