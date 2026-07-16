import unittest
from decimal import Decimal

from src.app.live_orchestrator import LiveStartResult
from src.app.live_runtime import LiveRuntime
from src.infra.alerts import InMemoryAlertSink
from src.infra.logging import InMemoryLogger
from src.preflight.checker import PreflightCheckResult, PreflightReport


class FakeRunner:
    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.result = result

    def run_cycle(
        self,
        rows,
        snapshot=None,
        current_drawdown=Decimal("0"),
        elapsed_hours=Decimal("0"),
    ):
        self.calls.append(
            (
                "run_cycle",
                {
                    "rows": rows,
                    "snapshot": snapshot,
                    "current_drawdown": current_drawdown,
                    "elapsed_hours": elapsed_hours,
                },
            )
        )
        if self.result is not None:
            return self.result
        return {"selected_symbol": rows[0]["symbol"] if rows else None}

    def consume_user_stream(self, **_kwargs) -> None:
        return None


class LiveObservabilityTests(unittest.TestCase):
    def test_runtime_loop_log_includes_risk_reasons(self) -> None:
        logger = InMemoryLogger()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=FakeRunner(
                result={
                    "selected_symbol": None,
                    "intent": {"action": "hold"},
                    "risk_reason": "uni_mmr_hard_limit",
                }
            ),
            event_source=lambda _: [],
            cycle_input_provider=lambda: [
                type(
                    "CycleInput",
                    (),
                    {
                        "rows": [{"symbol": "BTCUSDT"}],
                        "snapshot": None,
                        "current_drawdown": Decimal("0"),
                    },
                )()
            ],
            poll_interval_seconds=1.0,
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=1)

        loop_record = logger.records[-1]
        self.assertEqual(loop_record.event, "runtime.loop_completed")
        self.assertEqual(loop_record.context["risk_reasons"], ["uni_mmr_hard_limit"])

    def test_runtime_loop_log_includes_selected_symbols_and_intent_actions(self) -> None:
        logger = InMemoryLogger()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=FakeRunner(),
            event_source=lambda _: [],
            cycle_input_provider=lambda: [
                type(
                    "CycleInput",
                    (),
                    {
                        "rows": [{"symbol": "BTCUSDT"}],
                        "snapshot": None,
                        "current_drawdown": Decimal("0"),
                    },
                )()
            ],
            poll_interval_seconds=1.0,
            sleep_fn=lambda _: None,
            logger=logger,
        )

        runtime(max_loops=1)

        loop_record = logger.records[-1]
        self.assertEqual(loop_record.event, "runtime.loop_completed")
        self.assertEqual(loop_record.context["loop_count"], 1)
        self.assertEqual(loop_record.context["selected_symbols"], ["BTCUSDT"])
        self.assertEqual(loop_record.context["intent_actions"], [None])

    def test_runtime_logs_start_and_loop_progress(self) -> None:
        logger = InMemoryLogger()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="started",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=True)]
                ),
            ),
            live_runner=FakeRunner(),
            event_source=lambda _: [],
            cycle_input_provider=lambda: [],
            poll_interval_seconds=1.0,
            sleep_fn=lambda _: None,
            logger=logger,
        )

        result = runtime(max_loops=2)

        self.assertEqual(result.status, "started")
        self.assertEqual(
            [record.event for record in logger.records],
            [
                "runtime.startup_started",
                "runtime.startup_completed",
                "runtime.loop_completed",
                "runtime.loop_completed",
            ],
        )

    def test_runtime_alerts_when_startup_fails(self) -> None:
        logger = InMemoryLogger()
        alerts = InMemoryAlertSink()
        runtime = LiveRuntime(
            startup=lambda **_: LiveStartResult(
                status="preflight_failed",
                preflight_report=PreflightReport(
                    checks=[PreflightCheckResult(name="pm_account", ok=False, reason="denied")]
                ),
            ),
            live_runner=FakeRunner(),
            event_source=lambda _: [],
            cycle_input_provider=lambda: [],
            sleep_fn=lambda _: None,
            logger=logger,
            alert_sink=alerts,
        )

        result = runtime(max_loops=1)

        self.assertEqual(result.status, "preflight_failed")
        self.assertEqual(logger.records[-1].event, "runtime.startup_failed")
        self.assertEqual(alerts.messages[-1].channel, "runtime")


if __name__ == "__main__":
    unittest.main()
