from dataclasses import dataclass
from decimal import Decimal

from src.domain.enums import PositionSide


ZERO = Decimal("0")
BPS_DENOMINATOR = Decimal("10000")
ROUND_TRIPS = Decimal("2")


@dataclass(slots=True)
class HarvestDecision:
    should_harvest: bool
    net_pnl: Decimal
    gross_pnl: Decimal
    estimated_cost: Decimal
    reason: str | None


class HarvestRule:
    def __init__(
        self,
        taker_fee_bps: Decimal,
        slippage_bps: Decimal,
        min_net_pnl: Decimal,
    ) -> None:
        self._taker_fee_bps = taker_fee_bps
        self._slippage_bps = slippage_bps
        self._min_net_pnl = min_net_pnl

    def evaluate(
        self,
        symbol: str,
        side: PositionSide,
        unrealized_pnl: Decimal,
        target_notional: Decimal,
        min_safe_unimmr: Decimal,
        uni_mmr: Decimal,
        recent_funding_cost: Decimal,
    ) -> HarvestDecision:
        bps_cost = (self._taker_fee_bps + self._slippage_bps) / BPS_DENOMINATOR
        estimated_cost = target_notional * bps_cost * ROUND_TRIPS + recent_funding_cost
        net_pnl = unrealized_pnl - estimated_cost

        if uni_mmr < min_safe_unimmr:
            return HarvestDecision(
                should_harvest=False,
                net_pnl=net_pnl,
                gross_pnl=unrealized_pnl,
                estimated_cost=estimated_cost,
                reason="unimmr_below_harvest_floor",
            )
        if net_pnl < self._min_net_pnl:
            return HarvestDecision(
                should_harvest=False,
                net_pnl=net_pnl,
                gross_pnl=unrealized_pnl,
                estimated_cost=estimated_cost,
                reason="net_profit_below_threshold",
            )
        return HarvestDecision(
            should_harvest=True,
            net_pnl=net_pnl,
            gross_pnl=unrealized_pnl,
            estimated_cost=estimated_cost,
            reason=None,
        )
