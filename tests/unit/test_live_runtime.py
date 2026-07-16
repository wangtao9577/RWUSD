import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.app.bootstrap import build_live_runtime
from src.app.live_orchestrator import LiveCycleInput, LiveStartResult
from src.app.live_runtime import FileCycleInputProvider, LiveRuntime
from src.domain.models import ProfitBucket
from src.infra.alerts import InMemoryAlertSink
from src.infra.logging import InMemoryLogger
from src.preflight.checker import PreflightCheckResult, PreflightReport


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.result_payload = None
        self.profit_bucket = ProfitBucket()
        self.profit_bucket_sequence: list[ProfitBucket] = []
        self.restored_runtime_state = False
        self.consume_should_raise = False

    def run_cycle(
        self,
        rows,
        snapshot=None,
        current_drawdown=Decimal("0"),
        elapsed_hours=Decimal("0"),
    ):
        payload = {
            "rows": rows,
            "snapshot": snapshot,
            "current_drawdown": current_drawdown,
            "elapsed_hours": elapsed_hours,
        }
        self.calls.append(("run_cycle", payload))
        if self.profit_bucket_sequence:
            self.profit_bucket = self.profit_bucket_sequence.pop(0)
        if self.result_payload is not None:
            return self.result_payload
        return {"selected_symbol": rows[0]["symbol"] if rows else None}

    def consume_user_stream(
        self,
        event_source,
        keepalive_every: int = 50,
        max_events: int | None = None,
        retry_attempts: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ) -> int:
        payload = {
            "event_source": event_source,
            "keepalive_every": keepalive_every,
            "max_events": max_events,
            "retry_attempts": retry_attempts,
            "retry_backoff_seconds": retry_backoff_seconds,
            "retry_backoff_multiplier": retry_backoff_multiplier,
        }
        self.calls.append(("consume_user_stream", payload))
        if self.consume_should_raise:
            raise RuntimeError("user stream failed")
        return 0


class LiveRuntimeTests(unittest.TestCase):
    def test_runtime_stops_when_startup_does_not_start(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="preflight_failed",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=False)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [],
            sleep_fn=lambda _: None,
            logger=logger,
            alert_sink=alerts,
        )

        result = runtime(max_loops=2)

        self.assertEqual(result.status, "preflight_failed")
        self.assertEqual(result.loop_count, 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(logger.records[-1].event, "runtime.startup_failed")
        self.assertEqual(alerts.messages[-1].channel, "runtime")

    def test_runtime_runs_cycles_for_each_loop_and_sleeps_between_iterations(self) -> None:
        runner = FakeRunner()
        sleeps: list[float] = []
        logger = InMemoryLogger()
        batches = iter(
            [
                [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
                [LiveCycleInput(rows=[{"symbol": "ETHUSDT"}], current_drawdown=Decimal("0.01"))],
            ]
        )
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: next(batches),
            poll_interval_seconds=3.5,
            sleep_fn=sleeps.append,
            logger=logger,
        )

        result = runtime(max_loops=2)

        self.assertEqual(result.status, "started")
        self.assertEqual(result.loop_count, 2)
        self.assertEqual(len(result.loop_results), 2)
        self.assertEqual(
            [name for name, _ in runner.calls],
            ["consume_user_stream", "run_cycle", "run_cycle"],
        )
        self.assertEqual(runner.calls[1][1]["elapsed_hours"], Decimal("0"))
        self.assertEqual(
            runner.calls[2][1]["elapsed_hours"],
            Decimal("3.5") / Decimal("3600"),
        )
        self.assertEqual(sleeps, [3.5])
        self.assertEqual(
            [record.event for record in logger.records],
            [
                "runtime.startup_started",
                "runtime.startup_completed",
                "runtime.loop_completed",
                "runtime.loop_completed",
            ],
        )
        loop_contexts = [
            record.context
            for record in logger.records
            if record.event == "runtime.loop_completed"
        ]
        self.assertEqual(loop_contexts[0]["rebalance_actions"], [None])
        self.assertEqual(loop_contexts[0]["profit_sweep_count"], 0)
        self.assertEqual(loop_contexts[0]["redeem_topup_count"], 0)
        self.assertEqual(loop_contexts[0]["rwusd_principal"], "0")
        self.assertEqual(loop_contexts[0]["rwusd_interest_accrued"], "0")

    def test_runtime_starts_background_user_stream_after_startup(self) -> None:
        runner = FakeRunner()
        created_threads: list[object] = []

        class FakeThread:
            def __init__(self, target, daemon=None) -> None:
                self.target = target
                self.daemon = daemon
                self.started = False
                created_threads.append(self)

            def start(self) -> None:
                self.started = True
                self.target()

        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            thread_factory=FakeThread,
        )

        runtime(max_loops=1)

        self.assertEqual(len(created_threads), 1)
        self.assertTrue(created_threads[0].daemon)
        self.assertTrue(created_threads[0].started)
        consume_call = next(
            payload
            for name, payload in runner.calls
            if name == "consume_user_stream"
        )
        self.assertIsNone(consume_call["max_events"])

    def test_runtime_logs_and_alerts_when_background_user_stream_fails(self) -> None:
        runner = FakeRunner()
        runner.consume_should_raise = True
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()

        class FakeThread:
            def __init__(self, target, daemon=None) -> None:
                self.target = target

            def start(self) -> None:
                self.target()

        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            logger=logger,
            alert_sink=alerts,
            thread_factory=FakeThread,
        )

        runtime(max_loops=1)

        failure_record = next(
            record for record in logger.records if record.event == "runtime.user_stream_failed"
        )
        self.assertIn("user stream failed", failure_record.context["error"])
        self.assertEqual(alerts.messages[-1].channel, "runtime")

    def test_runtime_passes_zero_startup_stream_max_events_by_default(self) -> None:
        runner = FakeRunner()
        startup_calls: list[dict] = []

        def startup(**kwargs):
            startup_calls.append(kwargs)
            return LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            )

        class FakeThread:
            def __init__(self, target, daemon=None) -> None:
                self.target = target

            def start(self) -> None:
                return None

        runtime = LiveRuntime(
            startup=startup,
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            thread_factory=FakeThread,
        )

        runtime(max_loops=1)

        self.assertEqual(startup_calls[0]["startup_stream_max_events"], 0)

    def test_runtime_retries_cycle_input_provider_after_failure(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        provider_state = {"count": 0}

        def flaky_provider():
            provider_state["count"] += 1
            if provider_state["count"] == 1:
                raise RuntimeError("provider unavailable")
            return [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])]

        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=flaky_provider,
            sleep_fn=lambda _: None,
            logger=logger,
            alert_sink=alerts,
        )

        result = runtime(max_loops=1, cycle_retry_attempts=1)

        self.assertEqual(result.status, "started")
        self.assertEqual(
            [name for name, _ in runner.calls],
            ["consume_user_stream", "run_cycle"],
        )
        self.assertEqual(logger.records[2].event, "runtime.loop_retry")
        self.assertEqual(alerts.messages[-1].channel, "runtime")

    def test_runtime_uses_exponential_backoff_between_cycle_retries(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        sleeps: list[float] = []
        provider_state = {"count": 0}

        def flaky_provider():
            provider_state["count"] += 1
            if provider_state["count"] < 3:
                raise RuntimeError(f"provider unavailable {provider_state['count']}")
            return [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])]

        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=flaky_provider,
            sleep_fn=sleeps.append,
            logger=logger,
            alert_sink=alerts,
        )

        result = runtime(
            max_loops=1,
            cycle_retry_attempts=2,
            cycle_retry_backoff_seconds=0.5,
            cycle_retry_backoff_multiplier=2.0,
        )

        self.assertEqual(result.status, "started")
        self.assertEqual(
            [name for name, _ in runner.calls],
            ["consume_user_stream", "run_cycle"],
        )
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_runtime_loop_completed_context_includes_pdf_monitoring_fields(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        live_runner = type(
            "LiveRunnerStub",
            (),
            {
                "run_cycle": runner.run_cycle,
                "profit_bucket": ProfitBucket(
                    rwusd_principal=Decimal("80"),
                    rwusd_interest_accrued=Decimal("3.5"),
                    harvest_buffer=Decimal("12"),
                    closed_loop_ready=False,
                    last_rebalance_action="restore_later",
                    sweep_block_reason="pending_rebalance",
                    harvest_count=2,
                    deposit_count=1,
                    redeem_count=4,
                ),
            },
        )()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=live_runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=1)

        loop_record = next(
            record for record in logger.records if record.event == "runtime.loop_completed"
        )
        self.assertEqual(loop_record.context["rebalance_actions"], [None])
        self.assertEqual(loop_record.context["profit_sweep_count"], 1)
        self.assertEqual(loop_record.context["redeem_topup_count"], 4)
        self.assertEqual(loop_record.context["rwusd_principal"], "80")
        self.assertEqual(loop_record.context["rwusd_interest_accrued"], "3.5")
        self.assertEqual(loop_record.context["harvest_buffer"], "12")
        self.assertFalse(loop_record.context["closed_loop_ready"])
        self.assertEqual(loop_record.context["last_rebalance_action"], "restore_later")
        self.assertEqual(loop_record.context["sweep_block_reason"], "pending_rebalance")

    def test_runtime_loop_completed_context_reflects_profit_bucket_changes_across_dry_run_loops(self) -> None:
        runner = FakeRunner()
        runner.profit_bucket_sequence = [
            ProfitBucket(
                rwusd_principal=Decimal("120"),
                rwusd_redeemable=Decimal("120"),
                deposit_count=1,
                redeem_count=0,
            ),
            ProfitBucket(
                rwusd_principal=Decimal("40"),
                rwusd_redeemable=Decimal("40"),
                deposit_count=1,
                redeem_count=1,
            ),
        ]
        logger = InMemoryLogger()
        batches = iter(
            [
                [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
                [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            ]
        )
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: next(batches),
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=2)

        loop_contexts = [
            record.context
            for record in logger.records
            if record.event == "runtime.loop_completed"
        ]
        self.assertEqual(loop_contexts[0]["profit_sweep_count"], 1)
        self.assertEqual(loop_contexts[0]["redeem_topup_count"], 0)
        self.assertEqual(loop_contexts[0]["rwusd_principal"], "120")
        self.assertEqual(loop_contexts[1]["profit_sweep_count"], 0)
        self.assertEqual(loop_contexts[1]["redeem_topup_count"], 1)
        self.assertEqual(loop_contexts[1]["rwusd_principal"], "40")

    def test_runtime_does_not_recount_restored_profit_bucket_totals_on_first_loop(self) -> None:
        runner = FakeRunner()
        runner.profit_bucket = ProfitBucket(
            rwusd_principal=Decimal("10080"),
            rwusd_interest_accrued=Decimal("1.8"),
            deposit_count=4,
            redeem_count=1,
        )
        runner.restored_runtime_state = True
        logger = InMemoryLogger()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=1)

        loop_record = next(
            record for record in logger.records if record.event == "runtime.loop_completed"
        )
        self.assertEqual(loop_record.context["profit_sweep_count"], 0)
        self.assertEqual(loop_record.context["redeem_topup_count"], 0)
        self.assertEqual(loop_record.context["rwusd_principal"], "10080")
        self.assertEqual(loop_record.context["rwusd_interest_accrued"], "1.8")

    def test_runtime_loop_completed_context_flattens_multi_symbol_results(self) -> None:
        runner = FakeRunner()
        runner.result_payload = {
            "selected_symbol": "BTCUSDT",
            "selected_symbols": ["BTCUSDT", "ETHUSDT"],
        }
        logger = InMemoryLogger()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=runner,
            event_source=lambda _: [],
            cycle_input_provider=lambda: [LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=1)

        loop_record = next(
            record for record in logger.records if record.event == "runtime.loop_completed"
        )
        self.assertEqual(loop_record.context["selected_symbols"], ["BTCUSDT", "ETHUSDT"])


class FileCycleInputProviderTests(unittest.TestCase):
    def test_file_cycle_input_provider_reads_batches_sequentially(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload_path = Path(tmp_dir) / "cycles.json"
            payload_path.write_text(
                json.dumps(
                    [
                        [
                            {
                                "rows": [{"symbol": "BTCUSDT"}],
                                "current_drawdown": "0.00",
                            }
                        ],
                        [
                            {
                                "rows": [{"symbol": "ETHUSDT"}],
                                "current_drawdown": "0.02",
                            }
                        ],
                    ]
                ),
                encoding="utf-8",
            )

            provider = FileCycleInputProvider(payload_path)

            first_batch = provider()
            second_batch = provider()
            third_batch = provider()

            self.assertEqual(first_batch[0].rows[0]["symbol"], "BTCUSDT")
            self.assertEqual(first_batch[0].current_drawdown, Decimal("0.00"))
            self.assertEqual(second_batch[0].rows[0]["symbol"], "ETHUSDT")
            self.assertEqual(second_batch[0].current_drawdown, Decimal("0.02"))
            self.assertEqual(third_batch, [])

    def test_file_cycle_input_provider_reads_bundled_smoke_example(self) -> None:
        provider = FileCycleInputProvider("examples/live_cycle_inputs.smoke.json")

        first_batch = provider()
        second_batch = provider()
        third_batch = provider()

        self.assertEqual(len(first_batch), 1)
        self.assertEqual(len(first_batch[0].rows), 3)
        self.assertEqual(first_batch[0].rows[0]["symbol"], "BTCUSDT")
        self.assertEqual(second_batch[0].rows[1]["symbol"], "ETHUSDT")
        self.assertEqual(second_batch[0].current_drawdown, Decimal("0.01"))
        self.assertEqual(third_batch, [])


class BootstrapLiveRuntimeTests(unittest.TestCase):
    def test_build_live_runtime_returns_callable(self) -> None:
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
            cycle_file = Path(tmp_dir) / "cycles.json"
            cycle_file.write_text("[]", encoding="utf-8")

            runtime = build_live_runtime(config_path=env_file, cycle_inputs_path=cycle_file)

            self.assertTrue(callable(runtime))


if __name__ == "__main__":
    unittest.main()
