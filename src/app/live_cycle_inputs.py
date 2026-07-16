from collections.abc import Callable
from decimal import Decimal

from src.app.live_orchestrator import LiveCycleInput
from src.domain.models import PortfolioSnapshot


ZERO = Decimal("0")


class LiveMarketCycleInputProvider:
    def __init__(
        self,
        account_service,
        market_data_service,
        candidate_symbols: list[str],
        interval: str = "5m",
        kline_limit: int = 20,
        snapshot_provider: Callable[[], PortfolioSnapshot | None] | None = None,
    ) -> None:
        self._account_service = account_service
        self._market_data_service = market_data_service
        self._candidate_symbols = candidate_symbols
        self._interval = interval
        self._kline_limit = kline_limit
        self._snapshot_provider = snapshot_provider
        self._peak_equity: Decimal | None = None

    def __call__(self) -> list[LiveCycleInput]:
        snapshot = None
        current_drawdown = ZERO
        try:
            if self._snapshot_provider is not None:
                snapshot = self._snapshot_provider()
            else:
                risk_snapshot_provider = getattr(
                    self._account_service,
                    "get_pm_risk_snapshot",
                    None,
                )
                if callable(risk_snapshot_provider):
                    snapshot = risk_snapshot_provider()
                else:
                    snapshot = self._account_service.get_pm_account_snapshot()
            if snapshot is not None:
                current_drawdown = self._calculate_drawdown(snapshot.account_equity)
        except Exception:
            snapshot = None
            current_drawdown = ZERO
        rows = [self._build_row(symbol) for symbol in self._candidate_symbols]
        rows = self._normalize_rows(rows)
        return [
            LiveCycleInput(
                rows=rows,
                snapshot=snapshot,
                current_drawdown=current_drawdown,
            )
        ]

    def _calculate_drawdown(self, account_equity: Decimal) -> Decimal:
        if self._peak_equity is None or account_equity > self._peak_equity:
            self._peak_equity = account_equity
            return ZERO
        if self._peak_equity == ZERO:
            return ZERO
        return (self._peak_equity - account_equity) / self._peak_equity

    def _build_row(self, symbol: str) -> dict:
        klines = self._market_data_service.get_recent_klines(
            symbol=symbol,
            interval=self._interval,
            limit=self._kline_limit,
        )
        premium_index = self._market_data_service.get_premium_index(symbol)
        closes = [Decimal(str(item[4])) for item in klines]
        volumes = [Decimal(str(item[5])) for item in klines]
        notionals = [Decimal(str(item[7])) for item in klines]

        close = closes[-1]
        high_close = max(closes)
        low_close = min(closes)
        volatility = ZERO if close == ZERO else (high_close - low_close) / close
        liquidity = max(volumes) if volumes else ZERO
        margin = max(notionals) if notionals else ZERO
        funding = abs(Decimal(str(premium_index.get("lastFundingRate", "0"))))
        blocked = liquidity == ZERO or margin == ZERO

        return {
            "symbol": symbol,
            "close": close,
            "liquidity": liquidity,
            "volatility": volatility,
            "funding": funding,
            "margin": margin,
            "blocked": blocked,
        }

    def _normalize_rows(self, rows: list[dict]) -> list[dict]:
        normalized = [dict(row) for row in rows]
        for field in ("liquidity", "funding", "margin"):
            values = [Decimal(str(row[field])) for row in normalized]
            max_value = max(values) if values else ZERO
            for row in normalized:
                raw = Decimal(str(row[field]))
                if max_value == ZERO:
                    row[field] = ZERO
                    continue
                ratio = raw / max_value
                row[field] = self._compress_ratio(ratio)
        return normalized

    def _compress_ratio(self, value: Decimal) -> Decimal:
        if value <= ZERO:
            return ZERO
        return value.sqrt()
