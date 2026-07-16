from urllib.parse import urlsplit, urlunsplit

from src.exchange.binance_rest import BinanceRestClient


class BinanceMarketDataService:
    def __init__(self, rest: BinanceRestClient) -> None:
        self._rest = rest

    def get_recent_klines(self, symbol: str, interval: str, limit: int) -> dict:
        return self._rest.get(
            _resolve_public_futures_market_path(self._rest, "/fapi/v1/klines"),
            params={"symbol": symbol, "interval": interval, "limit": limit},
            signed=False,
        ).json()

    def get_premium_index(self, symbol: str) -> dict:
        return self._rest.get(
            _resolve_public_futures_market_path(self._rest, "/fapi/v1/premiumIndex"),
            params={"symbol": symbol},
            signed=False,
        ).json()


def _resolve_public_futures_market_path(rest: BinanceRestClient, path: str) -> str:
    base_url = getattr(rest, "_base_url", "")
    if not base_url:
        return path

    parsed = urlsplit(str(base_url))
    if parsed.netloc == "papi.binance.com":
        return urlunsplit(
            (
                parsed.scheme or "https",
                "fapi.binance.com",
                path,
                "",
                "",
            )
        )
    return path
