from dataclasses import dataclass, field
from decimal import Decimal

from src.domain.enums import StrategyPhase


ZERO = Decimal("0")
DEFAULT_SIM_LEVERAGE = Decimal("20")


@dataclass(slots=True)
class SymbolScore:
    symbol: str
    score: Decimal = ZERO
    liquidity_score: Decimal = ZERO
    volatility_score: Decimal = ZERO
    funding_score: Decimal = ZERO
    margin_efficiency_score: Decimal = ZERO
    execution_cost_score: Decimal = ZERO
    execution_cost_bps: Decimal = ZERO
    preferred_execution_symbol: str | None = None
    reject_reason: str | None = None


@dataclass(slots=True)
class StrategyPosition:
    symbol: str
    phase: StrategyPhase = StrategyPhase.IDLE
    long_qty: Decimal = ZERO
    short_qty: Decimal = ZERO
    long_entry_price: Decimal = ZERO
    short_entry_price: Decimal = ZERO
    long_unrealized_pnl: Decimal = ZERO
    short_unrealized_pnl: Decimal = ZERO


@dataclass(slots=True)
class PortfolioSnapshot:
    account_equity: Decimal
    available_balance: Decimal = ZERO
    uni_mmr: Decimal = ZERO
    total_abs_notional: Decimal = ZERO
    total_net_notional: Decimal = ZERO
    single_symbol_net_notional: Decimal = ZERO
    spot_usdt_balance: Decimal = ZERO
    spot_rwusd_balance: Decimal = ZERO
    bnb_balance: Decimal = ZERO


@dataclass(slots=True)
class ProfitBucket:
    realized_pnl_total: Decimal = ZERO
    realized_pnl_available_for_deposit: Decimal = ZERO
    harvest_buffer: Decimal = ZERO
    rwusd_principal: Decimal = ZERO
    rwusd_interest_accrued: Decimal = ZERO
    rwusd_redeemable: Decimal = ZERO
    harvest_count: int = 0
    deposit_count: int = 0
    redeem_count: int = 0
    closed_loop_ready: bool = False
    last_rebalance_action: str | None = None
    sweep_block_reason: str | None = None


@dataclass(slots=True)
class SelectorSnapshot:
    scores: list[SymbolScore] = field(default_factory=list)
    selected_symbol: str | None = None
    selected_symbols: list[str] = field(default_factory=list)
    cooldown_symbol: str | None = None
