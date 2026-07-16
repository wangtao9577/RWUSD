from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class FundingEstimate:
    amount: Decimal = ZERO
