from dataclasses import asdict, is_dataclass
from decimal import Decimal
import json
from pathlib import Path

from src.domain.models import PortfolioSnapshot


ZERO = Decimal("0")


def build_account_market_snapshot(
    account_snapshot,
    hedge_positions: list[object],
    market_rows: list[dict[str, object]],
    selected_symbols: list[str],
    strategy_state: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "account": _stringify_mapping(asdict(account_snapshot)),
        "positions": [
            _stringify_mapping(asdict(position))
            for position in hedge_positions
        ],
        "market": [_stringify_mapping(row) for row in market_rows],
        "selected_symbols": list(selected_symbols),
        "strategy": _stringify_mapping(strategy_state or {}),
    }


def collect_account_market_snapshot(
    account_service,
    market_data_service,
    candidate_symbols: list[str],
    interval: str,
    selected_symbols: list[str] | None = None,
    strategy_state: dict[str, object] | None = None,
    allow_account_fallback: bool = False,
    account_snapshot_override: PortfolioSnapshot | None = None,
    hedge_positions_override: list[object] | None = None,
) -> dict[str, object]:
    strategy_payload = dict(strategy_state or {})
    market_rows: list[dict[str, object]] = []
    for symbol in candidate_symbols:
        klines = market_data_service.get_recent_klines(symbol, interval, limit=2)
        premium_index = market_data_service.get_premium_index(symbol)
        last_kline = klines[-1] if klines else []
        market_rows.append(
            {
                "symbol": symbol,
                "interval": interval,
                "close_price": last_kline[4] if len(last_kline) > 4 else None,
                "volume": last_kline[5] if len(last_kline) > 5 else None,
                "funding_rate": premium_index.get("lastFundingRate"),
            }
        )

    if account_snapshot_override is not None:
        account_snapshot = account_snapshot_override
        hedge_positions = list(hedge_positions_override or [])
    else:
        try:
            account_snapshot = account_service.get_pm_account_snapshot()
            hedge_positions = account_service.get_um_hedge_positions()
        except Exception as exc:
            if not allow_account_fallback:
                raise
            account_snapshot = PortfolioSnapshot(account_equity=ZERO)
            hedge_positions = []
            strategy_payload["account_snapshot_fallback"] = True
            strategy_payload["account_snapshot_error"] = str(exc)

    return build_account_market_snapshot(
        account_snapshot=account_snapshot,
        hedge_positions=hedge_positions,
        market_rows=market_rows,
        selected_symbols=selected_symbols or [],
        strategy_state=strategy_payload,
    )


def write_account_market_snapshot(
    output_path: Path | str,
    account_service,
    market_data_service,
    candidate_symbols: list[str],
    interval: str,
    selected_symbols: list[str] | None = None,
    strategy_state: dict[str, object] | None = None,
    allow_account_fallback: bool = False,
    account_snapshot_override: PortfolioSnapshot | None = None,
    hedge_positions_override: list[object] | None = None,
) -> dict[str, object]:
    snapshot = collect_account_market_snapshot(
        account_service=account_service,
        market_data_service=market_data_service,
        candidate_symbols=candidate_symbols,
        interval=interval,
        selected_symbols=selected_symbols,
        strategy_state=strategy_state,
        allow_account_fallback=allow_account_fallback,
        account_snapshot_override=account_snapshot_override,
        hedge_positions_override=hedge_positions_override,
    )
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(snapshot, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return snapshot


def _stringify_mapping(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        payload[key] = _stringify_value(value)
    return payload


def _stringify_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_stringify_value(item) for item in value]
    if isinstance(value, dict):
        return _stringify_mapping(value)
    if is_dataclass(value):
        return _stringify_mapping(asdict(value))
    return value
