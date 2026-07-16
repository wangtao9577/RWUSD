import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from src.app.bootstrap import (
    _build_live_runner_instance,
    build_backtest_runner,
    build_live_sim_runtime,
    build_live_market_runtime,
    build_live_runner,
    build_live_runtime,
    build_live_user_stream_event_source,
    load_settings_from_env,
)
from src.config.loader import load_settings
from src.domain.models import ProfitBucket


class LoadSettingsTests(unittest.TestCase):
    def test_load_settings_rejects_missing_required_exchange_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_settings(env_file)

    def test_load_settings_rejects_blank_candidate_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=   ",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_settings(env_file)

    def test_load_settings_rejects_invalid_bool_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_DRY_RUN=flase",
                        "LIVE_LOG_ROTATE_DAILY=treu",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_settings(env_file)

    def test_load_settings_rejects_negative_retry_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_USER_STREAM_RETRY_ATTEMPTS=-1",
                        "LIVE_USER_STREAM_RETRY_BACKOFF_SECONDS=-0.5",
                        "LIVE_USER_STREAM_RETRY_BACKOFF_MULTIPLIER=0",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_settings(env_file)

    def test_load_settings_uses_schema_defaults_for_harvest_yield_and_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.positioning.sim_leverage, Decimal("20"))
            self.assertEqual(settings.harvest.min_net_pnl, Decimal("18"))
            self.assertEqual(settings.harvest.taker_fee_bps, Decimal("5"))
            self.assertEqual(settings.harvest.slippage_bps, Decimal("5"))
            self.assertEqual(settings.transfer.min_sweep, Decimal("50"))
            self.assertEqual(settings.transfer.pm_reserve, Decimal("100"))
            self.assertEqual(settings.transfer.min_redeem, Decimal("50"))
            self.assertEqual(settings.yield_config.rwusd_apr, Decimal("0.12"))
            self.assertEqual(settings.selector.eval_interval_minutes, 15)
            self.assertEqual(settings.selector.switch_edge, Decimal("0.20"))

    def test_load_settings_reads_harvest_yield_and_selector_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "HARVEST_MIN_NET_PNL=12.5",
                        "HARVEST_TAKER_FEE_BPS=4.2",
                        "HARVEST_SLIPPAGE_BPS=1.3",
                        "RWUSD_APR=0.065",
                        "SELECTOR_EVAL_INTERVAL_MINUTES=17",
                        "SELECTOR_SWITCH_EDGE=0.75",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.harvest.min_net_pnl, Decimal("12.5"))
            self.assertEqual(settings.harvest.taker_fee_bps, Decimal("4.2"))
            self.assertEqual(settings.harvest.slippage_bps, Decimal("1.3"))
            self.assertEqual(settings.yield_config.rwusd_apr, Decimal("0.065"))
            self.assertEqual(settings.selector.eval_interval_minutes, 17)
            self.assertEqual(settings.selector.switch_edge, Decimal("0.75"))

    def test_load_settings_reads_sim_leverage_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "SIM_LEVERAGE=33.5",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.positioning.sim_leverage, Decimal("33.5"))

    def test_profit_bucket_defaults_include_yield_and_operation_counters(self) -> None:
        bucket = ProfitBucket()

        self.assertEqual(bucket.rwusd_interest_accrued, Decimal("0"))
        self.assertEqual(bucket.harvest_count, 0)
        self.assertEqual(bucket.deposit_count, 0)
        self.assertEqual(bucket.redeem_count, 0)

    def test_load_settings_reads_candidate_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT, ETHUSDT ,SOLUSDT",
                        "PRIMARY_BAR_INTERVAL=5m",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.exchange.api_key, "test-key")
            self.assertEqual(
                settings.universe.candidate_symbols,
                ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            )
            self.assertEqual(settings.backtest.primary_bar_interval, "5m")

    def test_load_settings_reads_risk_threshold_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "RISK_SOFT_UNIMMR=7",
                        "RISK_HARD_UNIMMR=5",
                        "RISK_MAX_DRAWDOWN=0.08",
                        "RISK_MAX_TOTAL_ABS_LEVERAGE=10",
                        "RISK_MAX_TOTAL_NET_LEVERAGE=1",
                        "RISK_MAX_SINGLE_SYMBOL_NET_LEVERAGE=0.5",
                        "DRY_RUN_FILL_FRACTION=0.5",
                        "DRY_RUN_MIN_FILL_QUANTITY=0.01",
                        "DRY_RUN_ORDER_TIMEOUT_CYCLES=3",
                        "DRY_RUN_MAX_REQUOTES=2",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertIsInstance(settings.risk.soft_unimmr, Decimal)
            self.assertEqual(settings.risk.soft_unimmr, Decimal("7"))
            self.assertIsInstance(settings.risk.hard_unimmr, Decimal)
            self.assertEqual(settings.risk.hard_unimmr, Decimal("5"))
            self.assertIsInstance(settings.risk.max_drawdown, Decimal)
            self.assertEqual(settings.risk.max_drawdown, Decimal("0.08"))
            self.assertEqual(settings.risk.max_total_abs_leverage, Decimal("10"))
            self.assertEqual(settings.risk.max_total_net_leverage, Decimal("1"))
            self.assertEqual(settings.risk.max_single_symbol_net_leverage, Decimal("0.5"))
            self.assertEqual(settings.dry_run_execution.fill_fraction, Decimal("0.5"))
            self.assertEqual(settings.dry_run_execution.min_fill_quantity, Decimal("0.01"))
            self.assertEqual(settings.dry_run_execution.order_timeout_cycles, 3)
            self.assertEqual(settings.dry_run_execution.max_requotes, 2)

    def test_load_settings_reads_live_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_LOG_PATH=tmp/live_runtime.jsonl",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.live.log_path, "tmp/live_runtime.jsonl")

    def test_load_settings_reads_live_log_rotation_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_LOG_PATH=tmp/live_runtime.jsonl",
                        "LIVE_LOG_ROTATE_DAILY=true",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertTrue(settings.live.log_rotate_daily)

    def test_load_settings_reads_live_retry_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_USER_STREAM_RETRY_ATTEMPTS=3",
                        "LIVE_USER_STREAM_RETRY_BACKOFF_SECONDS=0.5",
                        "LIVE_USER_STREAM_RETRY_BACKOFF_MULTIPLIER=2.5",
                        "LIVE_CYCLE_RETRY_ATTEMPTS=4",
                        "LIVE_CYCLE_RETRY_BACKOFF_SECONDS=0.75",
                        "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER=3.0",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.live.user_stream_retry_attempts, 3)
            self.assertEqual(settings.live.user_stream_retry_backoff_seconds, 0.5)
            self.assertEqual(settings.live.user_stream_retry_backoff_multiplier, 2.5)
            self.assertEqual(settings.live.cycle_retry_attempts, 4)
            self.assertEqual(settings.live.cycle_retry_backoff_seconds, 0.75)
            self.assertEqual(settings.live.cycle_retry_backoff_multiplier, 3.0)

    def test_load_settings_reads_rwusd_and_rebalance_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "RISK_REDEEM_UNIMMR=9",
                        "TRANSFER_MIN_SWEEP=35",
                        "TRANSFER_PM_RESERVE=140",
                        "TRANSFER_MIN_REDEEM=70",
                        "LIVE_BULL_REBALANCE_DELAY_ENABLED=true",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertIsInstance(settings.risk.redeem_unimmr, Decimal)
            self.assertEqual(settings.risk.redeem_unimmr, Decimal("9"))
            self.assertEqual(settings.transfer.min_sweep, Decimal("35"))
            self.assertEqual(settings.transfer.pm_reserve, Decimal("140"))
            self.assertEqual(settings.transfer.min_redeem, Decimal("70"))
            self.assertTrue(settings.live.bull_rebalance_delay_enabled)

    def test_load_settings_reads_usdc_maker_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "USDC_MAKER_ENABLED=true",
                        "USDC_MAKER_ALLOWED_PHASES=open_hedge,restore_now,recover_missing_leg",
                        "USDC_MAKER_FALLBACK_TO_MARKET_ON_MISSING_PRICE=false",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertTrue(settings.usdc_maker.enabled)
            self.assertEqual(
                settings.usdc_maker.allowed_phases,
                ["open_hedge", "restore_now", "recover_missing_leg"],
            )
            self.assertFalse(settings.usdc_maker.fallback_to_market_on_missing_price)

    def test_build_live_runner_wires_usdc_maker_settings_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "USDC_MAKER_ENABLED=true",
                        "USDC_MAKER_ALLOWED_PHASES=open_hedge,restore_now",
                        "USDC_MAKER_FALLBACK_TO_MARKET_ON_MISSING_PRICE=false",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("src.app.bootstrap.BinanceRestClient", return_value="rest-client"), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceStreamClient",
                return_value="stream-client",
            ), patch(
                "src.app.bootstrap.SqliteStateStore",
                return_value="state-store",
            ), patch(
                "src.app.bootstrap.LiveRunner",
                return_value=type("FakeRunner", (), {"run_cycle": lambda self: None})(),
            ) as mocked_live_runner:
                _build_live_runner_instance(config_path=env_file)

            self.assertTrue(mocked_live_runner.call_args.kwargs["usdc_maker_enabled"])
            self.assertEqual(
                mocked_live_runner.call_args.kwargs["usdc_maker_allowed_phases"],
                {"open_hedge", "restore_now"},
            )
            self.assertFalse(
                mocked_live_runner.call_args.kwargs[
                    "usdc_maker_fallback_to_market_on_missing_price"
                ]
            )

    def test_build_live_runner_wires_redeem_and_transfer_thresholds_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "RISK_SOFT_UNIMMR=7",
                        "RISK_HARD_UNIMMR=5",
                        "RISK_MAX_DRAWDOWN=0.08",
                        "RISK_REDEEM_UNIMMR=9",
                        "TRANSFER_MIN_SWEEP=35",
                        "TRANSFER_PM_RESERVE=140",
                        "TRANSFER_MIN_REDEEM=70",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("src.app.bootstrap.BinanceRestClient", return_value="rest-client"), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceStreamClient",
                return_value="stream-client",
            ), patch(
                "src.app.bootstrap.SqliteStateStore",
                return_value="state-store",
            ), patch(
                "src.app.bootstrap.LiveRunner",
                return_value=type("FakeRunner", (), {"run_cycle": lambda self: None})(),
            ) as mocked_live_runner:
                build_live_runner(config_path=env_file)

            risk_manager = mocked_live_runner.call_args.kwargs["risk_manager"]
            transfer_planner = mocked_live_runner.call_args.kwargs["transfer_planner"]
            self.assertEqual(risk_manager._soft_unimmr, Decimal("7"))
            self.assertEqual(risk_manager._hard_unimmr, Decimal("5"))
            self.assertEqual(risk_manager._max_drawdown, Decimal("0.08"))
            self.assertEqual(risk_manager._redeem_unimmr, Decimal("9"))
            self.assertEqual(transfer_planner._min_sweep, Decimal("35"))
            self.assertEqual(transfer_planner._pm_reserve, Decimal("140"))
            self.assertEqual(transfer_planner._min_redeem, Decimal("70"))
            self.assertEqual(transfer_planner._redeem_unimmr, Decimal("9"))

    def test_load_settings_uses_schema_default_bar_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT, ETHUSDT ,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings(env_file)

            self.assertEqual(settings.backtest.primary_bar_interval, "5m")

    def test_bootstrap_load_settings_from_env_returns_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT, ETHUSDT ,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            settings = load_settings_from_env(env_file)

            self.assertEqual(settings.exchange.api_secret, "test-secret")
            self.assertEqual(settings.backtest.primary_bar_interval, "5m")

    def test_build_backtest_runner_returns_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            runner = build_backtest_runner(config_path=env_file)

            self.assertTrue(callable(runner))

    def test_build_live_runner_returns_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            runner = build_live_runner(config_path=env_file)
            try:
                self.assertTrue(callable(runner))
            finally:
                runner.__self__._state_store.close()

    def test_build_live_runner_uses_settings_sim_leverage_for_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "SIM_LEVERAGE=33",
                    ]
                ),
                encoding="utf-8",
            )

            runner = _build_live_runner_instance(config_path=env_file)
            try:
                self.assertEqual(runner._state.sim_leverage, Decimal("33"))
            finally:
                runner._state_store.close()

    def test_build_live_user_stream_event_source_returns_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            event_source = build_live_user_stream_event_source(config_path=env_file)

            self.assertTrue(callable(event_source))

    def test_build_live_runtime_uses_retry_defaults_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_CYCLE_RETRY_ATTEMPTS=2",
                        "LIVE_CYCLE_RETRY_BACKOFF_SECONDS=0.25",
                        "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER=1.5",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def __init__(self, **kwargs) -> None:
                    self.kwargs = kwargs
                    self.calls: list[dict] = []
                    self.retry_defaults: dict = {}

                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    merged = dict(self.retry_defaults)
                    merged.update(kwargs)
                    self.calls.append(merged)
                    return merged

            fake_runtime = FakeRuntime()

            with patch("src.app.bootstrap._build_live_orchestrator_instance", return_value="startup"), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ), patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.FileCycleInputProvider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ):
                runtime = build_live_runtime(config_path=env_file, cycle_inputs_path="cycles.json")
                result = runtime(max_loops=5)

            self.assertEqual(result["max_loops"], 5)
            self.assertEqual(result["cycle_retry_attempts"], 2)
            self.assertEqual(result["cycle_retry_backoff_seconds"], 0.25)
            self.assertEqual(result["cycle_retry_backoff_multiplier"], 1.5)

    def test_build_live_market_runtime_uses_retry_defaults_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_CYCLE_RETRY_ATTEMPTS=1",
                        "LIVE_CYCLE_RETRY_BACKOFF_SECONDS=0.4",
                        "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER=1.8",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def __init__(self, **kwargs) -> None:
                    self.kwargs = kwargs
                    self.calls: list[dict] = []
                    self.retry_defaults: dict = {}

                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    merged = dict(self.retry_defaults)
                    merged.update(kwargs)
                    self.calls.append(merged)
                    return merged

            fake_runtime = FakeRuntime()

            with patch("src.app.bootstrap._build_live_orchestrator_instance", return_value="startup"), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ), patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ):
                runtime = build_live_market_runtime(config_path=env_file)
                result = runtime(max_loops=4)

            self.assertEqual(result["max_loops"], 4)
            self.assertEqual(result["cycle_retry_attempts"], 1)
            self.assertEqual(result["cycle_retry_backoff_seconds"], 0.4)
            self.assertEqual(result["cycle_retry_backoff_multiplier"], 1.8)

    def test_build_live_sim_runtime_uses_market_provider_and_retry_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_CYCLE_RETRY_ATTEMPTS=1",
                        "LIVE_CYCLE_RETRY_BACKOFF_SECONDS=0.4",
                        "LIVE_CYCLE_RETRY_BACKOFF_MULTIPLIER=1.8",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def __init__(self, **kwargs) -> None:
                    self.kwargs = kwargs
                    self.calls: list[dict] = []
                    self.retry_defaults: dict = {}

                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    merged = dict(self.retry_defaults)
                    merged.update(kwargs)
                    self.calls.append(merged)
                    return merged

            fake_runtime = FakeRuntime()

            with patch("src.app.bootstrap._build_live_orchestrator_instance", return_value="startup"), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ), patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ):
                runtime = build_live_sim_runtime(config_path=env_file)
                result = runtime(max_loops=3)

            self.assertEqual(result["max_loops"], 3)
            self.assertEqual(result["cycle_retry_attempts"], 1)
            self.assertEqual(result["cycle_retry_backoff_seconds"], 0.4)
            self.assertEqual(result["cycle_retry_backoff_multiplier"], 1.8)

    def test_build_live_sim_runtime_forces_dry_run_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "PRIMARY_BAR_INTERVAL=5m",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def __init__(self, **kwargs) -> None:
                    self.kwargs = kwargs

                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    return type(
                        "Result",
                        (),
                        {
                            "status": "started",
                            "loop_results": [
                                {"selected_symbol": "BTCUSDT"},
                                {"selected_symbol": None},
                            ],
                        },
                    )()

            fake_runtime = FakeRuntime()
            runner_stub = type(
                "RunnerStub",
                (),
                {
                    "profit_bucket": type(
                        "ProfitBucketStub",
                        (),
                        {
                            "realized_pnl_total": Decimal("0"),
                            "realized_pnl_available_for_deposit": Decimal("0"),
                            "rwusd_principal": Decimal("10000"),
                            "rwusd_interest_accrued": Decimal("0"),
                            "rwusd_redeemable": Decimal("10000"),
                            "harvest_count": 0,
                            "deposit_count": 0,
                            "redeem_count": 0,
                        },
                    )(),
                },
            )()

            with patch("src.app.bootstrap._build_live_orchestrator_instance", return_value="startup"), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value=runner_stub,
            ) as mocked_runner_builder, patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceMarketDataService",
                return_value="market-data-service",
            ), patch(
                "src.app.bootstrap.write_runtime_summary",
                return_value={"ok": True},
            ) as mocked_write_summary, patch(
                "src.app.bootstrap.write_account_market_snapshot",
                return_value={"ok": True},
            ) as mocked_write_snapshot, patch(
                "src.app.bootstrap.write_simulation_outcome",
                return_value={"verdict": "pass"},
            ) as mocked_write_outcome:
                runtime = build_live_sim_runtime(config_path=env_file)
                result = runtime(max_loops=2)

            self.assertEqual(result.status, "started")
            runner_call = mocked_runner_builder.call_args
            self.assertEqual(runner_call.kwargs["config_path"], env_file)
            self.assertEqual(runner_call.kwargs["dry_run_override"], True)
            self.assertEqual(runner_call.kwargs["state_db_path"].name, "live_state.db")
            self.assertEqual(
                runner_call.kwargs["target_notional_override"],
                Decimal("100000"),
            )
            self.assertEqual(
                runner_call.kwargs["initial_profit_bucket"].rwusd_principal,
                Decimal("10000"),
            )
            mocked_write_summary.assert_called_once()
            mocked_write_snapshot.assert_called_once()
            mocked_write_outcome.assert_called_once()
            self.assertEqual(
                mocked_write_snapshot.call_args.kwargs["selected_symbols"],
                ["BTCUSDT"],
            )
            self.assertEqual(
                mocked_write_snapshot.call_args.kwargs["strategy_state"],
                {
                    "phase": "IDLE",
                    "leverage": Decimal("1"),
                    "long_entry": Decimal("0"),
                    "short_entry": Decimal("0"),
                    "long_unrealized": Decimal("0"),
                    "short_unrealized": Decimal("0"),
                    "take_profit_count": 0,
                    "restore_count": 0,
                    "rwusd_principal": "10000",
                    "rwusd_interest_accrued": "0",
                    "harvest_buffer": "0",
                    "closed_loop_ready": False,
                    "last_rebalance_action": None,
                    "sweep_block_reason": None,
                    "harvest_count": 0,
                    "deposit_count": 0,
                    "redeem_count": 0,
                },
            )
            self.assertTrue(
                mocked_write_snapshot.call_args.kwargs["allow_account_fallback"]
            )
            self.assertEqual(
                mocked_write_snapshot.call_args.kwargs["account_snapshot_override"].account_equity,
                Decimal("10000"),
            )
            self.assertEqual(
                mocked_write_snapshot.call_args.kwargs["account_snapshot_override"].spot_rwusd_balance,
                Decimal("10000"),
            )
            self.assertEqual(result.outcome, {"verdict": "pass"})

    def test_build_live_sim_runtime_uses_isolated_state_db_for_startup_and_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    return {"status": "started", "loop_results": []}

            fake_runtime = FakeRuntime()

            with patch(
                "src.app.bootstrap._build_live_orchestrator_instance",
                return_value="startup",
            ), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ) as mocked_runner_builder, patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceMarketDataService",
                return_value="market-data-service",
            ), patch(
                "src.app.bootstrap.write_runtime_summary",
                return_value={"ok": True},
            ), patch(
                "src.app.bootstrap.write_account_market_snapshot",
                return_value={"ok": True},
            ), patch(
                "src.app.bootstrap.write_simulation_outcome",
                return_value={"verdict": "pass"},
            ):
                fixed_now = datetime(2026, 6, 29, 15, 4, 5)
                runtime = build_live_sim_runtime(
                    config_path=env_file,
                    now_fn=lambda: fixed_now,
                )
                runtime(max_loops=1)

            isolated_state_path = Path("tmp/simulation/2026-06-29/150405/live_state.db")
            mocked_runner_builder.assert_any_call(
                config_path=env_file,
                logger=unittest.mock.ANY,
                dry_run_override=True,
                state_db_path=isolated_state_path,
                target_notional_override=Decimal("100000"),
                initial_profit_bucket=unittest.mock.ANY,
            )

    def test_build_live_sim_runtime_groups_outputs_under_timestamped_directory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "PRIMARY_BAR_INTERVAL=5m",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    return {
                        "status": "started",
                        "loop_results": [{"selected_symbol": "BTCUSDT"}],
                    }

            fake_runtime = FakeRuntime()
            fixed_now = datetime(2026, 6, 29, 15, 4, 5)

            with patch(
                "src.app.bootstrap._build_live_orchestrator_instance",
                return_value="startup",
            ), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ), patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceMarketDataService",
                return_value="market-data-service",
            ), patch(
                "src.app.bootstrap.write_runtime_summary",
                return_value={"ok": True},
            ) as mocked_write_summary, patch(
                "src.app.bootstrap.write_account_market_snapshot",
                return_value={"ok": True},
            ) as mocked_write_snapshot, patch(
                "src.app.bootstrap.write_simulation_outcome",
                return_value={"verdict": "pass"},
            ) as mocked_write_outcome:
                runtime = build_live_sim_runtime(config_path=env_file, now_fn=lambda: fixed_now)
                runtime(max_loops=1)

            expected_dir = Path("tmp/simulation/2026-06-29/150405")
            self.assertEqual(
                mocked_write_summary.call_args.kwargs["log_path"],
                expected_dir / "live_sim_runtime.jsonl",
            )
            self.assertEqual(
                mocked_write_outcome.call_args.kwargs["output_path"],
                expected_dir / "simulation-outcome.json",
            )

    def test_build_live_sim_runtime_collects_multi_symbol_results_for_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "PRIMARY_BAR_INTERVAL=5m",
                    ]
                ),
                encoding="utf-8",
            )

            class FakeRuntime:
                def set_retry_defaults(self, **kwargs) -> None:
                    self.retry_defaults = kwargs

                def __call__(self, **kwargs):
                    return type(
                        "Result",
                        (),
                        {
                            "status": "started",
                            "loop_results": [
                                {
                                    "selected_symbol": "BTCUSDT",
                                    "selected_symbols": ["BTCUSDT", "ETHUSDT"],
                                },
                                {"selected_symbol": None, "selected_symbols": []},
                            ],
                        },
                    )()

            fake_runtime = FakeRuntime()

            with patch(
                "src.app.bootstrap._build_live_orchestrator_instance",
                return_value="startup",
            ), patch(
                "src.app.bootstrap._build_live_runner_instance",
                return_value="runner",
            ), patch(
                "src.app.bootstrap.build_live_user_stream_event_source",
                return_value="event-source",
            ), patch(
                "src.app.bootstrap.build_live_market_cycle_input_provider",
                return_value="provider",
            ), patch(
                "src.app.bootstrap.LiveRuntime",
                return_value=fake_runtime,
            ), patch(
                "src.app.bootstrap.BinanceAccountService",
                return_value="account-service",
            ), patch(
                "src.app.bootstrap.BinanceMarketDataService",
                return_value="market-data-service",
            ), patch(
                "src.app.bootstrap.write_runtime_summary",
                return_value={"ok": True},
            ), patch(
                "src.app.bootstrap.write_account_market_snapshot",
                return_value={"ok": True},
            ) as mocked_write_snapshot, patch(
                "src.app.bootstrap.write_simulation_outcome",
                return_value={"verdict": "pass"},
            ):
                runtime = build_live_sim_runtime(config_path=env_file)
                runtime(max_loops=1)

            self.assertEqual(
                mocked_write_snapshot.call_args.kwargs["selected_symbols"],
                ["BTCUSDT", "ETHUSDT"],
            )


if __name__ == "__main__":
    unittest.main()
