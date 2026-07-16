from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class FillResult:
    realized_pnl: Decimal = ZERO
    fee_paid: Decimal = ZERO
