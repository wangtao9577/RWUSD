from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from src.app.bootstrap import build_live_orchestrator
from src.app.live_orchestrator import LiveCycleInput, LiveOrchestrator
from src.infra.alerts import InMemoryAlertSink
from src.infra.logging import InMemoryLogger
from src.preflight.checker import PreflightCheckResult, PreflightReport


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.reconcile_action: dict | None = None
        self.consume_result = 0
        self.cycle_results: list[dict] = []

    def restore_state(self) -> None:
        self.calls.append(("restore_state", None))

    def reconcile_remote_state(self) -> dict | None:
        self.calls.append(("reconcile_remote_state", None))
        return self.reconcile_action

    def consume_user_stream(
        self,
        event_source,
        keepalive_every: int = 50,
        max_events: int | None = None,
        retry_attempts: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ) -> int:
        self.calls.append(
            (
                "consume_user_stream",
                {
                    "event_source": event_source,
                    "keepalive_every": keepalive_every,
                    "max_events": max_events,
                    "retry_attempts": retry_attempts,
                    "retry_backoff_seconds": retry_backoff_seconds,
                    "retry_backoff_multiplier": retry_backoff_multiplier,
                },
            )
        )
        return self.consume_result

    def run_cycle(self, rows, snapshot=None, current_drawdown=Decimal("0")):
        payload = {
            "rows": rows,
            "snapshot": snapshot,
            "current_drawdown": current_drawdown,
        }
        self.calls.append(("run_cycle", payload))
        result = {"selected_symbol": None, "intent": "hold"}
        self.cycle_results.append(result)
        return result


class LiveOrchestratorTests(unittest.TestCase):
    def test_stops_immediately_when_preflight_fails(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        orchestrator = LiveOrchestrator(
            preflight=lambda: PreflightReport(
                checks=[PreflightCheckResult(name="pm_account", ok=False, reason="denied")]
            ),
            live_runner=runner,
            logger=logger,
            alert_sink=alerts,
        )

        result = orchestrator(event_source=lambda _: [], cycle_inputs=[])

        self.assertEqual(result.status, "preflight_failed")
        self.assertFalse(result.preflight_report.ok)
        self.assertEqual(runner.calls, [])
        self.assertEqual(logger.records[-1].event, "live.preflight_failed")
        self.assertEqual(alerts.messages[-1].channel, "live")

    def test_stops_when_reconcile_requires_manual_action(self) -> None:
        runner = FakeRunner()
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        runner.reconcile_action = {
            "action": "reconcile_required",
            "reason": "symbol_mismatch",
        }
        orchestrator = LiveOrchestrator(
            preflight=lambda: PreflightReport(
                checks=[PreflightCheckResult(name="pm_account", ok=True)]
            ),
            live_runner=runner,
            logger=logger,
            alert_sink=alerts,
        )

        result = orchestrator(event_source=lambda _: [], cycle_inputs=[])

        self.assertEqual(result.status, "reconcile_required")
        self.assertEqual(result.reconcile_action, runner.reconcile_action)
        self.assertEqual(
            runner.calls,
            [
                ("restore_state", None),
                ("reconcile_remote_state", None),
            ],
        )
        self.assertEqual(logger.records[-1].event, "live.reconcile_required")
        self.assertEqual(alerts.messages[-1].channel, "live")

    def test_runs_restore_reconcile_stream_and_cycles_in_order(self) -> None:
        runner = FakeRunner()
        runner.consume_result = 2
        orchestrator = LiveOrchestrator(
            preflight=lambda: PreflightReport(
                checks=[PreflightCheckResult(name="pm_account", ok=True)]
            ),
            live_runner=runner,
        )
        event_source = lambda _: [{"e": "one"}, {"e": "two"}]
        cycle_inputs = [
            LiveCycleInput(rows=[{"symbol": "BTCUSDT"}]),
            LiveCycleInput(
                rows=[{"symbol": "ETHUSDT"}],
                current_drawdown=Decimal("0.01"),
            ),
        ]

        result = orchestrator(
            event_source=event_source,
            cycle_inputs=cycle_inputs,
            keepalive_every=1,
            startup_stream_max_events=10,
        )

        self.assertEqual(result.status, "started")
        self.assertEqual(result.consumed_stream_events, 2)
        self.assertEqual(len(result.cycle_results), 2)
        self.assertEqual(
            [name for name, _ in runner.calls],
            [
                "restore_state",
                "reconcile_remote_state",
                "consume_user_stream",
                "run_cycle",
                "run_cycle",
            ],
        )

    def test_skips_startup_stream_consumption_when_max_events_is_zero(self) -> None:
        runner = FakeRunner()
        orchestrator = LiveOrchestrator(
            preflight=lambda: PreflightReport(
                checks=[PreflightCheckResult(name="pm_account", ok=True)]
            ),
            live_runner=runner,
        )

        result = orchestrator(
            event_source=lambda _: [{"e": "one"}],
            cycle_inputs=[LiveCycleInput(rows=[{"symbol": "BTCUSDT"}])],
            startup_stream_max_events=0,
        )

        self.assertEqual(result.status, "started")
        self.assertEqual(result.consumed_stream_events, 0)
        self.assertEqual(
            [name for name, _ in runner.calls],
            [
                "restore_state",
                "reconcile_remote_state",
                "run_cycle",
            ],
        )

    def test_passes_stream_retry_settings_to_runner(self) -> None:
        runner = FakeRunner()
        orchestrator = LiveOrchestrator(
            preflight=lambda: PreflightReport(
                checks=[PreflightCheckResult(name="pm_account", ok=True)]
            ),
            live_runner=runner,
        )

        orchestrator(
            event_source=lambda _: [],
            cycle_inputs=[],
            startup_stream_max_events=1,
            startup_stream_retry_attempts=2,
            startup_stream_retry_backoff_seconds=0.5,
            startup_stream_retry_backoff_multiplier=3.0,
        )

        consume_call = next(
            payload
            for name, payload in runner.calls
            if name == "consume_user_stream"
        )
        self.assertEqual(consume_call["retry_attempts"], 2)
        self.assertEqual(consume_call["retry_backoff_seconds"], 0.5)
        self.assertEqual(consume_call["retry_backoff_multiplier"], 3.0)


class BootstrapLiveOrchestratorTests(unittest.TestCase):
    def test_build_live_orchestrator_returns_callable(self) -> None:
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

            orchestrator = build_live_orchestrator(config_path=env_file)

            self.assertTrue(callable(orchestrator))


if __name__ == "__main__":
    unittest.main()
