"""Minimal accounting boundary for Task 5 portfolio responsibilities."""

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class RealizedPnlSummary:
    """Captures realized pnl totals without introducing exchange-side behavior."""

    realized_total: Decimal = ZERO


def summarize_realized_pnl(realized_total: Decimal = ZERO) -> RealizedPnlSummary:
    """Provides a tiny accounting seam for future strategy integration."""

    return RealizedPnlSummary(realized_total=realized_total)
