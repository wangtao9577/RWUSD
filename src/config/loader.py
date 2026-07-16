from pathlib import Path

from src.config.schema import Settings


def load_settings(env_file: str | Path) -> Settings:
    values = _read_env_file(Path(env_file))
    return Settings.model_validate(_build_settings_payload(values))


def _read_env_file(env_file: Path) -> dict[str, str]:
    content = env_file.read_text(encoding="utf-8")
    values: dict[str, str] = {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    return values


def _build_settings_payload(values: dict[str, str]) -> dict[str, object]:
    return {
        "exchange": {
            **({"api_key": values["BINANCE_API_KEY"]} if "BINANCE_API_KEY" in values else {}),
            **(
                {"api_secret": values["BINANCE_API_SECRET"]}
                if "BINANCE_API_SECRET" in values
                else {}
            ),
            **({"base_url": values["BINANCE_BASE_URL"]} if "BINANCE_BASE_URL" in values else {}),
            **(
                {"ws_base_url": values["BINANCE_WS_BASE_URL"]}
                if "BINANCE_WS_BASE_URL" in values
                else {}
            ),
        },
        "universe": {
            **(
                {"candidate_symbols": _parse_candidate_symbols(values["CANDIDATE_SYMBOLS"])}
                if "CANDIDATE_SYMBOLS" in values
                else {}
            ),
        },
        "backtest": {
            **(
                {"primary_bar_interval": values["PRIMARY_BAR_INTERVAL"]}
                if "PRIMARY_BAR_INTERVAL" in values
                else {}
            ),
        },
        "positioning": {
            **(
                {"target_notional": values["TARGET_NOTIONAL"]}
                if "TARGET_NOTIONAL" in values
                else {}
            ),
            **(
                {"sim_leverage": values["SIM_LEVERAGE"]}
                if "SIM_LEVERAGE" in values
                else {}
            ),
        },
        "take_profit": {
            **(
                {"long_take_profit": values["LONG_TAKE_PROFIT"]}
                if "LONG_TAKE_PROFIT" in values
                else {}
            ),
            **(
                {"short_take_profit": values["SHORT_TAKE_PROFIT"]}
                if "SHORT_TAKE_PROFIT" in values
                else {}
            ),
        },
        "risk": {
            **(
                {"soft_unimmr": values["RISK_SOFT_UNIMMR"]}
                if "RISK_SOFT_UNIMMR" in values
                else {}
            ),
            **(
                {"hard_unimmr": values["RISK_HARD_UNIMMR"]}
                if "RISK_HARD_UNIMMR" in values
                else {}
            ),
            **(
                {"max_drawdown": values["RISK_MAX_DRAWDOWN"]}
                if "RISK_MAX_DRAWDOWN" in values
                else {}
            ),
            **(
                {"redeem_unimmr": values["RISK_REDEEM_UNIMMR"]}
                if "RISK_REDEEM_UNIMMR" in values
                else {}
            ),
            **(
                {"max_total_abs_leverage": values["RISK_MAX_TOTAL_ABS_LEVERAGE"]}
                if "RISK_MAX_TOTAL_ABS_LEVERAGE" in values
                else {}
            ),
            **(
                {"max_total_net_leverage": values["RISK_MAX_TOTAL_NET_LEVERAGE"]}
                if "RISK_MAX_TOTAL_NET_LEVERAGE" in values
                else {}
            ),
            **(
                {
                    "max_single_symbol_net_leverage": values[
                        "RISK_MAX_SINGLE_SYMBOL_NET_LEVERAGE"
                    ]
                }
                if "RISK_MAX_SINGLE_SYMBOL_NET_LEVERAGE" in values
                else {}
            ),
        },
        "transfer": {
            **(
                {"min_sweep": values["TRANSFER_MIN_SWEEP"]}
                if "TRANSFER_MIN_SWEEP" in values
                else {}
            ),
            **(
                {"pm_reserve": values["TRANSFER_PM_RESERVE"]}
                if "TRANSFER_PM_RESERVE" in values
                else {}
            ),
            **(
                {"min_redeem": values["TRANSFER_MIN_REDEEM"]}
                if "TRANSFER_MIN_REDEEM" in values
                else {}
            ),
        },
        "harvest": {
            **(
                {"min_net_pnl": values["HARVEST_MIN_NET_PNL"]}
                if "HARVEST_MIN_NET_PNL" in values
                else {}
            ),
            **(
                {"taker_fee_bps": values["HARVEST_TAKER_FEE_BPS"]}
                if "HARVEST_TAKER_FEE_BPS" in values
                else {}
            ),
            **(
                {"slippage_bps": values["HARVEST_SLIPPAGE_BPS"]}
                if "HARVEST_SLIPPAGE_BPS" in values
                else {}
            ),
        },
        "yield_config": {
            **(
                {"rwusd_apr": values["RWUSD_APR"]}
                if "RWUSD_APR" in values
                else {}
            ),
        },
        "selector": {
            **(
                {"eval_interval_minutes": values["SELECTOR_EVAL_INTERVAL_MINUTES"]}
                if "SELECTOR_EVAL_INTERVAL_MINUTES" in values
                else {}
            ),
            **(
                {"switch_edge": values["SELECTOR_SWITCH_EDGE"]}
                if "SELECTOR_SWITCH_EDGE" in values
                else {}
            ),
        },
        "usdc_maker": {
            **(
                {
                    "enabled": _parse_bool(
                        values["USDC_MAKER_ENABLED"],
                        "USDC_MAKER_ENABLED",
                    )
                }
                if "USDC_MAKER_ENABLED" in values
                else {}
            ),
            **(
                {
                    "allowed_phases": _parse_csv_list(
                        values["USDC_MAKER_ALLOWED_PHASES"]
                    )
                }
                if "USDC_MAKER_ALLOWED_PHASES" in values
                else {}
            ),
            **(
                {
                    "fallback_to_market_on_missing_price": _parse_bool(
                        values["USDC_MAKER_FALLBACK_TO_MARKET_ON_MISSING_PRICE"],
                        "USDC_MAKER_FALLBACK_TO_MARKET_ON_MISSING_PRICE",
                    )
                }
                if "USDC_MAKER_FALLBACK_TO_MARKET_ON_MISSING_PRICE" in values
                else {}
            ),
        },
        "dry_run_execution": {
            **(
                {"fill_fraction": values["DRY_RUN_FILL_FRACTION"]}
                if "DRY_RUN_FILL_FRACTION" in values
                else {}
            ),
            **(
                {"min_fill_quantity": values["DRY_RUN_MIN_FILL_QUANTITY"]}
                if "DRY_RUN_MIN_FILL_QUANTITY" in values
                else {}
            ),
            **(
                {"order_timeout_cycles": values["DRY_RUN_ORDER_TIMEOUT_CYCLES"]}
                if "DRY_RUN_ORDER_TIMEOUT_CYCLES" in values
                else {}
            ),
            **(
                {"max_requotes": values["DRY_RUN_MAX_REQUOTES"]}
                if "DRY_RUN_MAX_REQUOTES" in values
                else {}
            ),
        },
        "live": {
            **(
                {
                    "dry_run": _parse_bool(
                        values["LIVE_DRY_RUN"],
                        "LIVE_DRY_RUN",
                    )
                }
                if "LIVE_DRY_RUN" in values
                else {}
            ),
            **(
                {"log_path": values["LIVE_LOG_PATH"]}
                if "LIVE_LOG_PATH" in values and values["LIVE_LOG_PATH"]
                else {}
            ),
            **(
                {
                    "log_rotate_daily": _parse_bool(
                        values["LIVE_LOG_ROTATE_DAILY"],
                        "LIVE_LOG_ROTATE_DAILY",
                    )
                }
                if "LIVE_LOG_ROTATE_DAILY" in values
                else {}
            ),
            **(
                {
                    "bull_rebalance_delay_enabled": _parse_bool(
                        values["LIVE_BULL_REBALANCE_DELAY_ENABLED"],
                        "LIVE_BULL_REBALANCE_DELAY_ENABLED",
                    )
                }
                if "LIVE_BULL_REBALANCE_DELAY_ENABLED" in values
                else {}
            ),
            **(
                {"user_stream_retry_attempts": values["LIVE_USER_STREAM_RETRY_ATTEMPTS"]}
                if "LIVE_USER_STREAM_RETRY_ATTEMPTS" in values
                else {}
            ),
            **(
                {"user_stream_retry_backoff_seconds": values["LIVE_USER_STREAM_RETRY_BACKOFF_SECONDS"]}
                if "LIVE_USER_STREAM_RETRY_BACKOFF_SECONDS" in values
                else {}
            ),
            **(
                {
                    "user_stream_retry_backoff_multiplier": values[
                        "LIVE_USER_STREAM_RETRY_BACKOFF_MULTIPLIER"
                    ]
                }
                if "LIVE_USER_STREAM_RETRY_BACKOFF_MULTIPLIER" in values
                else {}
            ),
            **(
                {"cycle_retry_attempts": values["LIVE_CYCLE_RETRY_ATTEMPTS"]}
                if "LIVE_CYCLE_RETRY_ATTEMPTS" in values
                else {}
            ),
            **(
                {"cycle_retry_backoff_seconds": values["LIVE_CYCLE_RETRY_BACKOFF_SECONDS"]}
                if "LIVE_CYCLE_RETRY_BACKOFF_SECONDS" in values
                else {}
            ),
            **(
                {
                    "cycle_retry_backoff_multiplier": values[
                        "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER"
                    ]
                }
                if "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER" in values
                else {}
            ),
        },
    }


def _parse_candidate_symbols(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _parse_csv_list(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _parse_bool(raw_value: str, field_name: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {field_name}: {raw_value!r}")
