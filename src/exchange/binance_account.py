from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from src.domain.models import PortfolioSnapshot
from src.exchange.binance_rest import BinanceRestClient
from src.strategy.position_sizing import OrderSizingRule


ZERO = Decimal("0")


@dataclass(slots=True, frozen=True)
class UmHedgePosition:
    symbol: str
    long_qty: Decimal = ZERO
    short_qty: Decimal = ZERO
    long_notional: Decimal = ZERO
    short_notional: Decimal = ZERO


def parse_pm_account(payload: dict) -> PortfolioSnapshot:
    available_balance = payload.get("availableBalance")
    if available_balance is None:
        available_balance = payload.get("totalAvailableBalance")
    return PortfolioSnapshot(
        account_equity=Decimal(payload["accountEquity"]),
        available_balance=Decimal(available_balance),
        uni_mmr=Decimal(payload["uniMMR"]),
    )


def parse_um_hedge_positions(payload: dict) -> list[UmHedgePosition]:
    grouped: dict[str, dict[str, Decimal | str]] = {}

    for raw_position in payload.get("positions", []):
        symbol = raw_position.get("symbol")
        position_side = raw_position.get("positionSide")
        if symbol is None or position_side not in {"LONG", "SHORT"}:
            continue

        qty = abs(Decimal(raw_position.get("positionAmt", "0")))
        notional = abs(Decimal(raw_position.get("notional", "0")))
        if qty == ZERO and notional == ZERO:
            continue

        current = grouped.setdefault(
            symbol,
            {
                "symbol": symbol,
                "long_qty": ZERO,
                "short_qty": ZERO,
                "long_notional": ZERO,
                "short_notional": ZERO,
            },
        )
        if position_side == "LONG":
            current["long_qty"] = qty
            current["long_notional"] = notional
        else:
            current["short_qty"] = qty
            current["short_notional"] = notional

    return [
        UmHedgePosition(
            symbol=str(item["symbol"]),
            long_qty=Decimal(item["long_qty"]),
            short_qty=Decimal(item["short_qty"]),
            long_notional=Decimal(item["long_notional"]),
            short_notional=Decimal(item["short_notional"]),
        )
        for item in grouped.values()
    ]


def build_pm_risk_snapshot(
    account_snapshot: PortfolioSnapshot,
    positions: list[UmHedgePosition],
) -> PortfolioSnapshot:
    total_abs_notional = ZERO
    total_net_notional = ZERO
    underlying_net_notionals: dict[str, Decimal] = {}
    for position in positions:
        total_abs_notional += position.long_notional + position.short_notional
        net_notional = position.long_notional - position.short_notional
        total_net_notional += net_notional
        underlying = _underlying_symbol(position.symbol)
        underlying_net_notionals[underlying] = (
            underlying_net_notionals.get(underlying, ZERO) + net_notional
        )

    single_symbol_net_notional = max(
        (abs(value) for value in underlying_net_notionals.values()),
        default=ZERO,
    )
    return PortfolioSnapshot(
        account_equity=account_snapshot.account_equity,
        available_balance=account_snapshot.available_balance,
        uni_mmr=account_snapshot.uni_mmr,
        total_abs_notional=total_abs_notional,
        total_net_notional=total_net_notional,
        single_symbol_net_notional=single_symbol_net_notional,
        spot_usdt_balance=account_snapshot.spot_usdt_balance,
        spot_rwusd_balance=account_snapshot.spot_rwusd_balance,
        bnb_balance=account_snapshot.bnb_balance,
    )


class BinanceAccountService:
    def __init__(self, rest: BinanceRestClient) -> None:
        self._rest = rest

    def get_pm_account(self) -> dict:
        return self._rest.get("/papi/v1/account", signed=True).json()

    def get_pm_account_snapshot(self) -> PortfolioSnapshot:
        return parse_pm_account(self.get_pm_account())

    def get_pm_risk_snapshot(self) -> PortfolioSnapshot:
        return build_pm_risk_snapshot(
            account_snapshot=self.get_pm_account_snapshot(),
            positions=self.get_um_hedge_positions(),
        )

    def get_um_hedge_positions(self) -> list[UmHedgePosition]:
        payload = self._rest.get("/papi/v1/um/account", signed=True).json()
        return parse_um_hedge_positions(payload)

    def get_um_position_mode(self) -> dict:
        return self._rest.get("/papi/v1/um/positionSide/dual", signed=True).json()

    def get_symbol_order_sizing_rule(self, symbol: str) -> OrderSizingRule:
        exchange_info_path = _resolve_exchange_info_path(self._rest)
        payload = self._rest.get(exchange_info_path).json()
        symbol_info = next(item for item in payload["symbols"] if item["symbol"] == symbol)
        filters = {item["filterType"]: item for item in symbol_info["filters"]}
        lot_size = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        min_notional_filter = filters["MIN_NOTIONAL"]

        return OrderSizingRule(
            step_size=Decimal(lot_size["stepSize"]),
            min_qty=Decimal(lot_size["minQty"]),
            min_notional=Decimal(
                min_notional_filter.get("notional", min_notional_filter.get("minNotional", "0"))
            ),
        )

    def set_um_position_mode(self, dual_side_position: bool) -> dict:
        return self._rest.post(
            "/papi/v1/um/positionSide/dual",
            params={
                "dualSidePosition": "true" if dual_side_position else "false",
            },
            signed=True,
        ).json()

    def place_um_order(
        self,
        symbol: str,
        side: str,
        position_side: str,
        order_type: str,
        quantity: str,
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
    ) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": quantity,
        }
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if reduce_only is not None:
            params["reduceOnly"] = "true" if reduce_only else "false"
        return self._rest.post(
            "/papi/v1/um/order",
            params=params,
            signed=True,
        ).json()

    def close_position(
        self,
        symbol: str,
        position_side: str,
        quantity: str,
    ) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return self._rest.post(
            "/papi/v1/um/order",
            params={
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": quantity,
                "reduceOnly": "true",
            },
            signed=True,
        ).json()

    def transfer_pm_to_spot(self, asset: str, amount: str) -> dict:
        return self._rest.post(
            "/sapi/v1/asset/transfer",
            params={"asset": asset, "amount": amount},
            signed=True,
        ).json()

    def transfer_spot_to_pm(self, asset: str, amount: str) -> dict:
        return self._rest.post(
            "/sapi/v1/asset/transfer",
            params={"asset": asset, "amount": amount},
            signed=True,
        ).json()

    def subscribe_rwusd(self, amount: str) -> dict:
        return self._rest.post(
            "/sapi/v1/rwusd/subscribe",
            params={"amount": amount},
            signed=True,
        ).json()

    def redeem_rwusd(self, amount: str) -> dict:
        return self._rest.post(
            "/sapi/v1/rwusd/redeem",
            params={"amount": amount},
            signed=True,
        ).json()


def _resolve_exchange_info_path(rest: BinanceRestClient) -> str:
    base_url = getattr(rest, "_base_url", "")
    if not base_url:
        return "/fapi/v1/exchangeInfo"

    parsed = urlsplit(str(base_url))
    if parsed.netloc == "papi.binance.com":
        return urlunsplit(
            (
                parsed.scheme or "https",
                "fapi.binance.com",
                "/fapi/v1/exchangeInfo",
                "",
                "",
            )
        )
    return "/fapi/v1/exchangeInfo"


def _underlying_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    for suffix in ("USDT", "USDC"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized
