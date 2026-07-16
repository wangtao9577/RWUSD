"""Minimal portfolio state boundary used by strategy orchestration."""

from dataclasses import dataclass
from decimal import Decimal

from src.domain.enums import StrategyPhase
from src.domain.models import DEFAULT_SIM_LEVERAGE


ZERO = Decimal("0")


@dataclass(slots=True)
class HedgeState:
    """Tracks the symbol and notionals held by the hedge workflow."""

    underlying_symbol: str | None = None
    symbol: str | None = None
    long_symbol: str | None = None
    short_symbol: str | None = None
    phase: StrategyPhase = StrategyPhase.IDLE
    long_notional: Decimal = ZERO
    short_notional: Decimal = ZERO
    long_filled: bool = False
    short_filled: bool = False
    sim_leverage: Decimal = DEFAULT_SIM_LEVERAGE
    sim_long_qty: Decimal = ZERO
    sim_short_qty: Decimal = ZERO
    sim_long_entry_price: Decimal = ZERO
    sim_short_entry_price: Decimal = ZERO
    sim_long_unrealized_pnl: Decimal = ZERO
    sim_short_unrealized_pnl: Decimal = ZERO
    sim_last_mark_price: Decimal = ZERO
    sim_take_profit_count: int = 0
    sim_restore_count: int = 0
    sim_cycle_id: int = 0
    last_symbol_switch_minute: int | None = None
