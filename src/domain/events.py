from dataclasses import dataclass
from decimal import Decimal

from src.domain.enums import PositionSide


ZERO = Decimal("0")


@dataclass(slots=True)
class ProfitTriggerEvent:
    symbol: str
    side: PositionSide
    unrealized_pnl: Decimal = ZERO


@dataclass(slots=True)
class RiskPauseEvent:
    reason: str
    uni_mmr: Decimal = ZERO


@dataclass(slots=True)
class HarvestExecutedEvent:
    symbol: str
    side: PositionSide
    gross_pnl: Decimal = ZERO
    net_pnl: Decimal = ZERO
    estimated_cost: Decimal = ZERO
