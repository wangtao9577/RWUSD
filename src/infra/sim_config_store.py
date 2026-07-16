from pathlib import Path

from src.config.loader import _read_env_file


DEFAULT_SIM_CONFIG: dict[str, str] = {
    "BINANCE_API_KEY": "replace-with-server-key",
    "BINANCE_API_SECRET": "replace-with-server-secret",
    "BINANCE_BASE_URL": "https://papi.binance.com",
    "BINANCE_WS_BASE_URL": "",
    "CANDIDATE_SYMBOLS": "BTCUSDT,ETHUSDT,SOLUSDT",
    "PRIMARY_BAR_INTERVAL": "5m",
    "TARGET_NOTIONAL": "1000",
    "LONG_TAKE_PROFIT": "25",
    "SHORT_TAKE_PROFIT": "25",
    "LIVE_DRY_RUN": "true",
    "LIVE_LOG_PATH": "tmp/server/live_runtime.jsonl",
    "LIVE_LOG_ROTATE_DAILY": "false",
    "LIVE_USER_STREAM_RETRY_ATTEMPTS": "3",
    "LIVE_USER_STREAM_RETRY_BACKOFF_SECONDS": "1.0",
    "LIVE_USER_STREAM_RETRY_BACKOFF_MULTIPLIER": "2.0",
    "LIVE_CYCLE_RETRY_ATTEMPTS": "3",
    "LIVE_CYCLE_RETRY_BACKOFF_SECONDS": "1.0",
    "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER": "2.0",
    "RISK_SOFT_UNIMMR": "6",
    "RISK_HARD_UNIMMR": "4",
    "RISK_MAX_DRAWDOWN": "0.15",
}

_OUTPUT_ORDER = list(DEFAULT_SIM_CONFIG.keys())


def save_simulation_config(
    *,
    env_file: str | Path,
    api_key: str,
    api_secret: str,
) -> dict[str, object]:
    normalized_api_key = api_key.strip()
    normalized_api_secret = api_secret.strip()
    if not normalized_api_key:
        raise ValueError("api_key must not be blank")
    if not normalized_api_secret:
        raise ValueError("api_secret must not be blank")
    if len(normalized_api_key) < 16:
        raise ValueError("api_key looks invalid: too short")
    if len(normalized_api_secret) < 16:
        raise ValueError("api_secret looks invalid: too short")

    env_path = Path(env_file)
    merged_values = _load_merged_values(env_path)
    merged_values["BINANCE_API_KEY"] = normalized_api_key
    merged_values["BINANCE_API_SECRET"] = normalized_api_secret
    env_path.write_text(_render_env_content(merged_values), encoding="utf-8")
    return build_config_status(env_file=env_path)


def build_config_status(*, env_file: str | Path) -> dict[str, object]:
    env_path = Path(env_file)
    values = _load_merged_values(env_path)
    api_key = values.get("BINANCE_API_KEY", "").strip()
    api_secret = values.get("BINANCE_API_SECRET", "").strip()
    candidate_symbols = [
        item.strip()
        for item in values.get("CANDIDATE_SYMBOLS", "").split(",")
        if item.strip()
    ]
    return {
        "env_file_path": str(env_path),
        "api_key_configured": bool(api_key) and api_key != DEFAULT_SIM_CONFIG["BINANCE_API_KEY"],
        "api_secret_configured": bool(api_secret)
        and api_secret != DEFAULT_SIM_CONFIG["BINANCE_API_SECRET"],
        "api_key_masked": _mask_api_key(api_key),
        "live_dry_run": values.get("LIVE_DRY_RUN", "").strip().lower() == "true",
        "live_log_path": values.get("LIVE_LOG_PATH", ""),
        "candidate_symbols": candidate_symbols,
    }


def _load_merged_values(env_file: Path) -> dict[str, str]:
    values = dict(DEFAULT_SIM_CONFIG)
    if env_file.exists():
        values.update(_read_env_file(env_file))
    return values


def _render_env_content(values: dict[str, str]) -> str:
    ordered_keys = _OUTPUT_ORDER + [
        key for key in values.keys() if key not in _OUTPUT_ORDER
    ]
    return "\n".join(f"{key}={values[key]}" for key in ordered_keys) + "\n"


def _mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 5:
        return "***"
    return f"{api_key[:3]}***{api_key[-2:]}"
