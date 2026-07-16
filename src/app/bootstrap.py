from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.backtest.engine import BacktestEngine
from src.app.live_cycle_inputs import LiveMarketCycleInputProvider
from src.app.live_orchestrator import LiveOrchestrator, LiveStartResult
from src.app.live_runner import LiveRunner
from src.app.live_runtime import FileCycleInputProvider, LiveRuntime
from src.config.loader import load_settings
from src.config.schema import Settings
from src.domain.models import PortfolioSnapshot, ProfitBucket
from src.exchange.binance_account import BinanceAccountService
from src.exchange.binance_market import BinanceMarketDataService
from src.exchange.binance_rest import BinanceRestClient
from src.exchange.binance_stream import (
    BinanceStreamClient,
    BinanceUserStreamEventSource,
    derive_user_stream_ws_base_url,
)
from src.infra.logging import (
    CompositeLogger,
    DatePartitionedJsonlFileLogger,
    InMemoryLogger,
    JsonlFileLogger,
)
from src.infra.persistence import SqliteStateStore
from src.infra.simulation_outcome import write_simulation_outcome
from src.infra.simulation_report import write_runtime_summary
from src.preflight.checker import PreflightChecker, PreflightReport
from src.portfolio.transfers import TransferPlanner
from src.risk.rules import RiskRuleSet
from src.app.simulation_snapshot import write_account_market_snapshot
from src.strategy.dry_run_order_lifecycle import DryRunMatchingEngine


DEFAULT_SIM_INITIAL_CAPITAL_USDT = Decimal("10000")
DEFAULT_SIM_UNI_MMR = Decimal("99999999")
_SIMULATION_BAR_INTERVAL_SECONDS = {
    "1m": 60.0,
    "3m": 180.0,
    "5m": 300.0,
    "15m": 900.0,
    "30m": 1800.0,
    "1h": 3600.0,
    "2h": 7200.0,
    "4h": 14400.0,
    "1d": 86400.0,
}


def simulation_poll_interval_seconds(primary_bar_interval: str) -> float:
    normalized_interval = str(primary_bar_interval).strip().lower()
    try:
        return _SIMULATION_BAR_INTERVAL_SECONDS[normalized_interval]
    except KeyError as exc:
        raise ValueError(
            f"unsupported simulation bar interval: {primary_bar_interval}"
        ) from exc


def load_settings_from_env(env_file: str | Path = ".env") -> Settings:
    return load_settings(env_file)


def build_backtest_runner(config_path: Path | str | None = None):
    load_settings(config_path or ".env")

    engine = BacktestEngine()

    def run() -> BacktestEngine:
        return engine

    return run


def build_live_runner(config_path: Path | str | None = None):
    runner = _build_live_runner_instance(config_path=config_path)

    return runner.run_cycle


def build_live_preflight(config_path: Path | str | None = None):
    settings = load_settings(config_path or ".env")
    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    stream_client = BinanceStreamClient(rest_client)
    checker = PreflightChecker(
        account_service=account_service,
        stream_client=stream_client,
        candidate_symbols=settings.universe.candidate_symbols,
    )

    return checker.run


def build_live_user_stream_event_source(config_path: Path | str | None = None):
    settings = load_settings(config_path or ".env")
    ws_base_url = settings.exchange.ws_base_url or derive_user_stream_ws_base_url(
        settings.exchange.base_url
    )
    return BinanceUserStreamEventSource(ws_base_url=ws_base_url)


def build_live_orchestrator(config_path: Path | str | None = None):
    preflight = build_live_preflight(config_path=config_path)
    logger = _build_live_logger(config_path=config_path)
    live_runner = _build_live_runner_instance(config_path=config_path, logger=logger)
    orchestrator = LiveOrchestrator(
        preflight=preflight,
        live_runner=live_runner,
        logger=logger,
    )
    return orchestrator


def build_live_runtime(
    config_path: Path | str | None = None,
    cycle_inputs_path: Path | str = "tmp/live_cycle_inputs.json",
):
    settings = load_settings(config_path or ".env")
    logger = _build_live_logger(config_path=config_path)
    orchestrator = _build_live_orchestrator_instance(
        config_path=config_path,
        logger=logger,
    )
    live_runner = _build_live_runner_instance(config_path=config_path, logger=logger)
    event_source = build_live_user_stream_event_source(config_path=config_path)
    cycle_input_provider = FileCycleInputProvider(cycle_inputs_path)
    runtime = LiveRuntime(
        startup=orchestrator,
        live_runner=live_runner,
        event_source=event_source,
        cycle_input_provider=cycle_input_provider,
        logger=logger,
    )
    runtime.set_retry_defaults(
        cycle_retry_attempts=settings.live.cycle_retry_attempts,
        cycle_retry_backoff_seconds=settings.live.cycle_retry_backoff_seconds,
        cycle_retry_backoff_multiplier=settings.live.cycle_retry_backoff_multiplier,
        stream_retry_attempts=settings.live.user_stream_retry_attempts,
        stream_retry_backoff_seconds=settings.live.user_stream_retry_backoff_seconds,
        stream_retry_backoff_multiplier=settings.live.user_stream_retry_backoff_multiplier,
    )
    return runtime


def build_live_market_cycle_input_provider(
    config_path: Path | str | None = None,
    snapshot_provider=None,
):
    settings = load_settings(config_path or ".env")
    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    market_data_service = BinanceMarketDataService(rest_client)
    return LiveMarketCycleInputProvider(
        account_service=account_service,
        market_data_service=market_data_service,
        candidate_symbols=settings.universe.candidate_symbols,
        interval=settings.backtest.primary_bar_interval,
        snapshot_provider=snapshot_provider,
    )


def build_live_market_runtime(config_path: Path | str | None = None):
    settings = load_settings(config_path or ".env")
    logger = _build_live_logger(config_path=config_path)
    orchestrator = _build_live_orchestrator_instance(
        config_path=config_path,
        logger=logger,
    )
    live_runner = _build_live_runner_instance(config_path=config_path, logger=logger)
    event_source = build_live_user_stream_event_source(config_path=config_path)
    cycle_input_provider = build_live_market_cycle_input_provider(config_path=config_path)
    runtime = LiveRuntime(
        startup=orchestrator,
        live_runner=live_runner,
        event_source=event_source,
        cycle_input_provider=cycle_input_provider,
        logger=logger,
    )
    runtime.set_retry_defaults(
        cycle_retry_attempts=settings.live.cycle_retry_attempts,
        cycle_retry_backoff_seconds=settings.live.cycle_retry_backoff_seconds,
        cycle_retry_backoff_multiplier=settings.live.cycle_retry_backoff_multiplier,
        stream_retry_attempts=settings.live.user_stream_retry_attempts,
        stream_retry_backoff_seconds=settings.live.user_stream_retry_backoff_seconds,
        stream_retry_backoff_multiplier=settings.live.user_stream_retry_backoff_multiplier,
    )
    return runtime


def build_live_sim_runtime(
    config_path: Path | str | None = None,
    log_path: Path | str | None = None,
    snapshot_output_path: Path | str | None = None,
    summary_output_path: Path | str | None = None,
    now_fn=None,
):
    settings = load_settings(config_path or ".env")
    resolved_log_path, resolved_snapshot_output_path, resolved_summary_output_path = (
        _resolve_live_sim_output_paths(
            log_path=log_path,
            snapshot_output_path=snapshot_output_path,
            summary_output_path=summary_output_path,
            now_fn=now_fn,
        )
    )
    logger = _build_live_sim_logger(log_path=resolved_log_path)
    initial_capital = DEFAULT_SIM_INITIAL_CAPITAL_USDT
    sim_target_notional = _build_live_sim_target_notional(
        initial_capital=initial_capital,
        sim_leverage=settings.positioning.sim_leverage,
    )
    state_db_path = resolved_log_path.parent / "live_state.db"
    live_runner = _build_live_runner_instance(
        config_path=config_path,
        logger=logger,
        dry_run_override=True,
        state_db_path=state_db_path,
        target_notional_override=sim_target_notional,
        initial_profit_bucket=ProfitBucket(
            rwusd_principal=initial_capital,
            rwusd_redeemable=initial_capital,
        ),
    )
    startup = _build_live_sim_startup(live_runner)
    event_source = _build_noop_user_stream_event_source()
    cycle_input_provider = build_live_market_cycle_input_provider(
        config_path=config_path,
        snapshot_provider=lambda: _build_live_sim_account_snapshot(
            live_runner=live_runner,
            initial_capital=initial_capital,
        ),
    )
    runtime = LiveRuntime(
        startup=startup,
        live_runner=live_runner,
        event_source=event_source,
        cycle_input_provider=cycle_input_provider,
        poll_interval_seconds=simulation_poll_interval_seconds(
            settings.backtest.primary_bar_interval
        ),
        logger=logger,
        enable_background_user_stream=False,
    )
    runtime.set_retry_defaults(
        cycle_retry_attempts=settings.live.cycle_retry_attempts,
        cycle_retry_backoff_seconds=settings.live.cycle_retry_backoff_seconds,
        cycle_retry_backoff_multiplier=settings.live.cycle_retry_backoff_multiplier,
        stream_retry_attempts=settings.live.user_stream_retry_attempts,
        stream_retry_backoff_seconds=settings.live.user_stream_retry_backoff_seconds,
        stream_retry_backoff_multiplier=settings.live.user_stream_retry_backoff_multiplier,
    )

    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    market_data_service = BinanceMarketDataService(rest_client)

    def run(max_loops: int | None = None):
        result = runtime(max_loops=max_loops)
        if _extract_runtime_status(result) == "started":
            loop_results = _extract_runtime_loop_results(result)
            selected_symbols = _extract_runtime_selected_symbols(loop_results)
            summary = write_runtime_summary(
                log_path=resolved_log_path,
                output_path=resolved_summary_output_path,
            )
            snapshot = write_account_market_snapshot(
                output_path=resolved_snapshot_output_path,
                account_service=account_service,
                market_data_service=market_data_service,
                candidate_symbols=settings.universe.candidate_symbols,
                interval=settings.backtest.primary_bar_interval,
                selected_symbols=selected_symbols,
                strategy_state=_build_simulation_strategy_state(live_runner),
                allow_account_fallback=True,
                account_snapshot_override=_build_live_sim_account_snapshot(
                    live_runner=live_runner,
                    initial_capital=initial_capital,
                ),
                hedge_positions_override=[],
            )
            outcome = write_simulation_outcome(
                summary=summary,
                snapshot=snapshot,
                output_path=resolved_summary_output_path.parent / "simulation-outcome.json",
                initial_capital_usdt=initial_capital,
            )
            _attach_runtime_outcome(result, outcome)
        return result

    return run


def _build_live_sim_startup(live_runner: LiveRunner):
    def startup(**kwargs) -> LiveStartResult:
        live_runner.restore_state()
        return LiveStartResult(
            status="started",
            preflight_report=PreflightReport(checks=[]),
            consumed_stream_events=0,
            cycle_results=[],
        )

    return startup


def _build_noop_user_stream_event_source():
    def event_source(listen_key: str):
        return []

    return event_source


def run(env_file: str | Path = ".env") -> BacktestEngine:
    runner = build_backtest_runner(config_path=env_file)
    return runner()


def main(env_file: str | Path = ".env") -> Settings:
    return load_settings_from_env(env_file)


def _build_live_runner_instance(
    config_path: Path | str | None = None,
    logger=None,
    dry_run_override: bool | None = None,
    state_db_path: Path | str | None = None,
    target_notional_override: Decimal | None = None,
    initial_profit_bucket: ProfitBucket | None = None,
) -> LiveRunner:
    settings = load_settings(config_path or ".env")
    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    stream_client = BinanceStreamClient(rest_client)
    state_store = SqliteStateStore(Path(state_db_path or "tmp/live_state.db"))
    risk_manager = RiskRuleSet(
        soft_unimmr=settings.risk.soft_unimmr,
        hard_unimmr=settings.risk.hard_unimmr,
        max_drawdown=settings.risk.max_drawdown,
        redeem_unimmr=settings.risk.redeem_unimmr,
        reserve_available_balance=settings.transfer.pm_reserve,
        max_total_abs_leverage=settings.risk.max_total_abs_leverage,
        max_total_net_leverage=settings.risk.max_total_net_leverage,
        max_single_symbol_net_leverage=settings.risk.max_single_symbol_net_leverage,
    )
    transfer_planner = TransferPlanner(
        min_sweep=settings.transfer.min_sweep,
        pm_reserve=settings.transfer.pm_reserve,
        min_redeem=settings.transfer.min_redeem,
        redeem_unimmr=settings.risk.redeem_unimmr,
    )
    return LiveRunner(
        account_service=account_service,
        stream_client=stream_client,
        candidate_symbols=settings.universe.candidate_symbols,
        target_notional=(
            settings.positioning.target_notional
            if target_notional_override is None
            else target_notional_override
        ),
        sim_leverage=settings.positioning.sim_leverage,
        long_take_profit=settings.take_profit.long_take_profit,
        short_take_profit=settings.take_profit.short_take_profit,
        dry_run=settings.live.dry_run if dry_run_override is None else dry_run_override,
        state_store=state_store,
        risk_manager=risk_manager,
        transfer_planner=transfer_planner,
        logger=logger,
        initial_profit_bucket=initial_profit_bucket,
        usdc_maker_enabled=settings.usdc_maker.enabled,
        usdc_maker_allowed_phases=set(settings.usdc_maker.allowed_phases),
        usdc_maker_fallback_to_market_on_missing_price=(
            settings.usdc_maker.fallback_to_market_on_missing_price
        ),
        dry_run_matching_engine=DryRunMatchingEngine(
            fill_fraction=settings.dry_run_execution.fill_fraction,
            min_fill_quantity=settings.dry_run_execution.min_fill_quantity,
        ),
        dry_run_order_timeout_cycles=settings.dry_run_execution.order_timeout_cycles,
        dry_run_max_requotes=settings.dry_run_execution.max_requotes,
    )


def _build_live_orchestrator_instance(
    config_path: Path | str | None,
    logger,
    state_db_path: Path | str | None = None,
    dry_run_override: bool | None = None,
):
    preflight = build_live_preflight(config_path=config_path)
    live_runner = _build_live_runner_instance(
        config_path=config_path,
        logger=logger,
        dry_run_override=dry_run_override,
        state_db_path=state_db_path,
    )
    return LiveOrchestrator(
        preflight=preflight,
        live_runner=live_runner,
        logger=logger,
    )


def _build_live_logger(config_path: Path | str | None = None):
    settings = load_settings(config_path or ".env")
    memory_logger = InMemoryLogger()
    if not settings.live.log_path:
        return memory_logger
    file_logger = (
        DatePartitionedJsonlFileLogger(settings.live.log_path)
        if settings.live.log_rotate_daily
        else JsonlFileLogger(settings.live.log_path)
    )
    return CompositeLogger(
        [
            memory_logger,
            file_logger,
        ]
    )


def _build_live_sim_logger(log_path: Path | str):
    memory_logger = InMemoryLogger()
    file_logger = JsonlFileLogger(log_path)
    return CompositeLogger(
        [
            memory_logger,
            file_logger,
        ]
    )


def _build_simulation_strategy_state(live_runner: object) -> dict[str, object]:
    state = getattr(live_runner, "_state", None)
    profit_bucket = getattr(live_runner, "profit_bucket", None)
    strategy_state = {
        "phase": _stringify_phase(getattr(state, "phase", getattr(live_runner, "phase", "IDLE"))),
        "leverage": getattr(state, "sim_leverage", Decimal("1")),
        "long_entry": getattr(state, "sim_long_entry_price", Decimal("0")),
        "short_entry": getattr(state, "sim_short_entry_price", Decimal("0")),
        "long_unrealized": getattr(state, "sim_long_unrealized_pnl", Decimal("0")),
        "short_unrealized": getattr(state, "sim_short_unrealized_pnl", Decimal("0")),
        "take_profit_count": int(getattr(state, "sim_take_profit_count", 0) or 0),
        "restore_count": int(getattr(state, "sim_restore_count", 0) or 0),
        "rwusd_principal": "0",
        "rwusd_interest_accrued": "0",
        "harvest_buffer": "0",
        "closed_loop_ready": False,
        "last_rebalance_action": None,
        "sweep_block_reason": None,
        "harvest_count": 0,
        "deposit_count": 0,
        "redeem_count": 0,
    }
    if profit_bucket is None:
        return strategy_state

    strategy_state.update(
        {
        "rwusd_principal": str(getattr(profit_bucket, "rwusd_principal", Decimal("0"))),
        "rwusd_interest_accrued": str(
            getattr(profit_bucket, "rwusd_interest_accrued", Decimal("0"))
        ),
        "harvest_buffer": str(
            getattr(
                profit_bucket,
                "harvest_buffer",
                getattr(
                    profit_bucket,
                    "realized_pnl_available_for_deposit",
                    Decimal("0"),
                ),
            )
        ),
        "closed_loop_ready": bool(
            getattr(profit_bucket, "closed_loop_ready", False)
        ),
        "last_rebalance_action": getattr(
            profit_bucket,
            "last_rebalance_action",
            None,
        ),
        "sweep_block_reason": getattr(
            profit_bucket,
            "sweep_block_reason",
            None,
        ),
        "harvest_count": int(getattr(profit_bucket, "harvest_count", 0) or 0),
        "deposit_count": int(getattr(profit_bucket, "deposit_count", 0) or 0),
        "redeem_count": int(getattr(profit_bucket, "redeem_count", 0) or 0),
        }
    )
    return strategy_state


def _build_live_sim_account_snapshot(
    *,
    live_runner: object,
    initial_capital: Decimal,
) -> PortfolioSnapshot:
    state = getattr(live_runner, "_state", None)
    profit_bucket = getattr(live_runner, "profit_bucket", None)

    long_unrealized = Decimal(str(getattr(state, "sim_long_unrealized_pnl", Decimal("0"))))
    short_unrealized = Decimal(str(getattr(state, "sim_short_unrealized_pnl", Decimal("0"))))
    realized_total = Decimal(str(getattr(profit_bucket, "realized_pnl_total", Decimal("0"))))
    realized_buffer = Decimal(
        str(
            getattr(
                profit_bucket,
                "harvest_buffer",
                Decimal("0"),
            )
        ),
    )
    rwusd_principal = Decimal(str(getattr(profit_bucket, "rwusd_principal", Decimal("0"))))
    rwusd_interest = Decimal(str(getattr(profit_bucket, "rwusd_interest_accrued", Decimal("0"))))
    mark_price = Decimal(str(getattr(state, "sim_last_mark_price", Decimal("0"))))
    long_qty = Decimal(str(getattr(state, "sim_long_qty", Decimal("0"))))
    short_qty = Decimal(str(getattr(state, "sim_short_qty", Decimal("0"))))

    total_unrealized = long_unrealized + short_unrealized
    account_equity = initial_capital + realized_total + rwusd_interest + total_unrealized
    available_balance = initial_capital + realized_buffer
    total_abs_notional = mark_price * (abs(long_qty) + abs(short_qty))
    total_net_notional = mark_price * (long_qty - short_qty)

    return PortfolioSnapshot(
        account_equity=max(Decimal("0"), account_equity),
        available_balance=max(Decimal("0"), available_balance),
        uni_mmr=DEFAULT_SIM_UNI_MMR,
        total_abs_notional=max(Decimal("0"), total_abs_notional),
        total_net_notional=total_net_notional,
        single_symbol_net_notional=total_net_notional,
        spot_usdt_balance=max(Decimal("0"), realized_buffer),
        spot_rwusd_balance=max(Decimal("0"), rwusd_principal),
    )


def _build_live_sim_target_notional(
    *,
    initial_capital: Decimal,
    sim_leverage: Decimal,
) -> Decimal:
    return (initial_capital * sim_leverage) / Decimal("2")


def _stringify_phase(phase: object) -> str:
    if hasattr(phase, "value"):
        return str(getattr(phase, "value"))
    rendered = str(phase or "").strip()
    return rendered or "IDLE"


def _attach_runtime_outcome(result: object, outcome: dict[str, object]) -> None:
    if isinstance(result, dict):
        result["outcome"] = outcome
        return
    try:
        setattr(result, "outcome", outcome)
    except (AttributeError, TypeError):
        return


def _extract_runtime_selected_symbol(result: object):
    if hasattr(result, "selected_symbol"):
        return getattr(result, "selected_symbol")
    if isinstance(result, dict):
        return result.get("selected_symbol")
    return None


def _extract_runtime_selected_symbols(results: list[object]) -> list[object]:
    selected_symbols: list[object] = []
    for result in results:
        many = _extract_runtime_selected_symbols_from_result(result)
        if many:
            selected_symbols.extend(many)
            continue
        single = _extract_runtime_selected_symbol(result)
        if single is not None:
            selected_symbols.append(single)
    return selected_symbols


def _extract_runtime_selected_symbols_from_result(result: object) -> list[object]:
    if hasattr(result, "selected_symbols"):
        value = getattr(result, "selected_symbols")
        return [symbol for symbol in value if symbol is not None] if isinstance(value, list) else []
    if isinstance(result, dict):
        value = result.get("selected_symbols")
        return [symbol for symbol in value if symbol is not None] if isinstance(value, list) else []
    return []


def _extract_runtime_status(result: object):
    if hasattr(result, "status"):
        return getattr(result, "status")
    if isinstance(result, dict):
        return result.get("status")
    return None


def _extract_runtime_loop_results(result: object):
    if hasattr(result, "loop_results"):
        return getattr(result, "loop_results")
    if isinstance(result, dict):
        return result.get("loop_results", [])
    return []


def _resolve_live_sim_output_paths(
    log_path: Path | str | None,
    snapshot_output_path: Path | str | None,
    summary_output_path: Path | str | None,
    now_fn=None,
) -> tuple[Path, Path, Path]:
    if log_path is not None and snapshot_output_path is not None and summary_output_path is not None:
        return Path(log_path), Path(snapshot_output_path), Path(summary_output_path)

    stamp = (now_fn or datetime.now)()
    run_dir = Path("tmp/simulation") / stamp.strftime("%Y-%m-%d") / stamp.strftime("%H%M%S")
    return (
        Path(log_path) if log_path is not None else run_dir / "live_sim_runtime.jsonl",
        Path(snapshot_output_path) if snapshot_output_path is not None else run_dir / "account-market-snapshot.json",
        Path(summary_output_path) if summary_output_path is not None else run_dir / "runtime-summary.json",
    )
