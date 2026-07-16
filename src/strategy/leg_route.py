from dataclasses import dataclass
from decimal import Decimal

from src.exchange.binance_account import UmHedgePosition


USDT_SUFFIX = "USDT"
USDC_SUFFIX = "USDC"


@dataclass(slots=True, frozen=True)
class RemoteHedgeRoute:
    underlying_symbol: str
    anchor_symbol: str
    long_symbol: str
    short_symbol: str
    long_notional: Decimal
    short_notional: Decimal


@dataclass(slots=True, frozen=True)
class RestoreExecutionRoute:
    symbol: str
    order_type: str
    price: Decimal | None = None
    time_in_force: str | None = None
    reduce_only: bool | None = None
    maker_only: bool = False


@dataclass(slots=True, frozen=True)
class ExecutionRoute:
    anchor_symbol: str
    execution_stage: str
    symbol: str
    order_type: str
    price: Decimal | None = None
    time_in_force: str | None = None
    reduce_only: bool | None = None
    maker_only: bool = False
    fallback_reason: str | None = None


@dataclass(slots=True, frozen=True)
class ExecutionPreference:
    anchor_symbol: str
    preferred_symbol: str
    execution_cost_bps: Decimal
    maker_only: bool = False


def derive_underlying_symbol(symbol: str) -> str:
    if symbol.endswith(USDT_SUFFIX):
        return symbol[: -len(USDT_SUFFIX)]
    if symbol.endswith(USDC_SUFFIX):
        return symbol[: -len(USDC_SUFFIX)]
    return symbol


def resolve_restore_symbol(
    current_symbol: str,
    available_symbols: set[str],
) -> str:
    preferred = to_usdc_symbol(current_symbol)
    if preferred != current_symbol and preferred in available_symbols:
        return preferred
    return current_symbol


def build_restore_execution_route(
    current_symbol: str,
    available_symbols: set[str],
    reference_price: Decimal | None,
) -> RestoreExecutionRoute:
    symbol = resolve_restore_symbol(
        current_symbol=current_symbol,
        available_symbols=available_symbols,
    )
    if symbol.endswith(USDC_SUFFIX) and reference_price is not None:
        return RestoreExecutionRoute(
            symbol=symbol,
            order_type="LIMIT",
            price=reference_price,
            time_in_force="GTX",
            reduce_only=False,
            maker_only=True,
        )
    return RestoreExecutionRoute(
        symbol=symbol,
        order_type="MARKET",
        price=None,
        time_in_force=None,
        reduce_only=None,
        maker_only=False,
    )


def build_execution_preference(
    anchor_symbol: str,
    available_symbols: set[str],
    default_taker_cost_bps: Decimal = Decimal("5"),
) -> ExecutionPreference:
    preferred_symbol = resolve_restore_symbol(
        current_symbol=anchor_symbol,
        available_symbols=available_symbols,
    )
    uses_usdc_maker = preferred_symbol.endswith(USDC_SUFFIX) and preferred_symbol != anchor_symbol
    return ExecutionPreference(
        anchor_symbol=anchor_symbol,
        preferred_symbol=preferred_symbol,
        execution_cost_bps=Decimal("0") if uses_usdc_maker else default_taker_cost_bps,
        maker_only=uses_usdc_maker,
    )


def build_execution_route(
    execution_stage: str,
    anchor_symbol: str,
    available_symbols: set[str],
    reference_price: Decimal | None,
    maker_enabled: bool,
    maker_allowed_phases: set[str],
    fallback_to_market_on_missing_price: bool,
) -> ExecutionRoute:
    if not maker_enabled:
        return ExecutionRoute(
            anchor_symbol=anchor_symbol,
            execution_stage=execution_stage,
            symbol=anchor_symbol,
            order_type="MARKET",
            fallback_reason="maker_disabled",
        )

    if execution_stage not in maker_allowed_phases:
        return ExecutionRoute(
            anchor_symbol=anchor_symbol,
            execution_stage=execution_stage,
            symbol=anchor_symbol,
            order_type="MARKET",
            fallback_reason="stage_not_allowed",
        )

    preferred_symbol = resolve_restore_symbol(
        current_symbol=anchor_symbol,
        available_symbols=available_symbols,
    )
    if preferred_symbol == anchor_symbol:
        return ExecutionRoute(
            anchor_symbol=anchor_symbol,
            execution_stage=execution_stage,
            symbol=anchor_symbol,
            order_type="MARKET",
            fallback_reason="usdc_symbol_unavailable",
        )

    if reference_price is None:
        if fallback_to_market_on_missing_price:
            return ExecutionRoute(
                anchor_symbol=anchor_symbol,
                execution_stage=execution_stage,
                symbol=anchor_symbol,
                order_type="MARKET",
                fallback_reason="missing_reference_price",
            )
        return ExecutionRoute(
            anchor_symbol=anchor_symbol,
            execution_stage=execution_stage,
            symbol=preferred_symbol,
            order_type="MARKET",
            fallback_reason="missing_reference_price",
        )

    return ExecutionRoute(
        anchor_symbol=anchor_symbol,
        execution_stage=execution_stage,
        symbol=preferred_symbol,
        order_type="LIMIT",
        price=reference_price,
        time_in_force="GTX",
        reduce_only=False,
        maker_only=True,
    )


def group_remote_hedges(
    positions: list[UmHedgePosition],
    allowed_symbols: set[str] | None = None,
) -> list[RemoteHedgeRoute]:
    grouped: dict[str, dict[str, object]] = {}

    for position in positions:
        if allowed_symbols is not None and position.symbol not in allowed_symbols:
            continue
        underlying_symbol = derive_underlying_symbol(position.symbol)
        current = grouped.setdefault(
            underlying_symbol,
            {
                "symbols": set(),
                "long_symbol": None,
                "short_symbol": None,
                "long_notional": Decimal("0"),
                "short_notional": Decimal("0"),
            },
        )
        symbols = current["symbols"]
        assert isinstance(symbols, set)
        symbols.add(position.symbol)
        if position.long_qty > Decimal("0"):
            current["long_symbol"] = position.symbol
            current["long_notional"] = position.long_notional
        if position.short_qty > Decimal("0"):
            current["short_symbol"] = position.symbol
            current["short_notional"] = position.short_notional

    routes: list[RemoteHedgeRoute] = []
    for underlying_symbol, current in grouped.items():
        long_symbol = current["long_symbol"]
        short_symbol = current["short_symbol"]
        if long_symbol is None or short_symbol is None:
            continue
        symbols = current["symbols"]
        assert isinstance(symbols, set)
        routes.append(
            RemoteHedgeRoute(
                underlying_symbol=underlying_symbol,
                anchor_symbol=select_anchor_symbol(list(symbols)),
                long_symbol=str(long_symbol),
                short_symbol=str(short_symbol),
                long_notional=Decimal(str(current["long_notional"])),
                short_notional=Decimal(str(current["short_notional"])),
            )
        )
    return routes


def select_anchor_symbol(symbols: list[str]) -> str:
    usdt_symbol = next((symbol for symbol in symbols if symbol.endswith(USDT_SUFFIX)), None)
    if usdt_symbol is not None:
        return usdt_symbol
    return sorted(symbols)[0]


def to_usdc_symbol(symbol: str) -> str:
    if symbol.endswith(USDT_SUFFIX):
        return f"{symbol[: -len(USDT_SUFFIX)]}{USDC_SUFFIX}"
    return symbol
