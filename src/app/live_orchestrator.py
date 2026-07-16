from dataclasses import dataclass, field
from decimal import Decimal

from src.infra.alerts import InMemoryAlertSink
from src.infra.logging import InMemoryLogger
from src.preflight.checker import PreflightReport


@dataclass(slots=True)
class LiveCycleInput:
    rows: list[dict]
    snapshot: object | None = None
    current_drawdown: Decimal = Decimal("0")


@dataclass(slots=True)
class LiveStartResult:
    status: str
    preflight_report: PreflightReport
    reconcile_action: dict | None = None
    consumed_stream_events: int = 0
    cycle_results: list[object] = field(default_factory=list)


class LiveOrchestrator:
    def __init__(self, preflight, live_runner, logger=None, alert_sink=None) -> None:
        self._preflight = preflight
        self._live_runner = live_runner
        self._logger = logger or InMemoryLogger()
        self._alert_sink = alert_sink or InMemoryAlertSink()

    def __call__(
        self,
        event_source,
        cycle_inputs: list[LiveCycleInput],
        keepalive_every: int = 50,
        startup_stream_max_events: int | None = 0,
        startup_stream_retry_attempts: int = 0,
        startup_stream_retry_backoff_seconds: float = 1.0,
        startup_stream_retry_backoff_multiplier: float = 2.0,
    ) -> LiveStartResult:
        preflight_report = self._preflight()
        if not preflight_report.ok:
            self._logger.log(
                level="ERROR",
                message="live preflight failed",
                event="live.preflight_failed",
            )
            self._alert_sink.send(channel="live", body="preflight_failed")
            return LiveStartResult(
                status="preflight_failed",
                preflight_report=preflight_report,
            )

        self._live_runner.restore_state()
        reconcile_action = self._live_runner.reconcile_remote_state()
        if reconcile_action is not None:
            self._logger.log(
                level="WARN",
                message="live reconcile required",
                event="live.reconcile_required",
                context={"action": reconcile_action},
            )
            self._alert_sink.send(channel="live", body="reconcile_required")
            return LiveStartResult(
                status="reconcile_required",
                preflight_report=preflight_report,
                reconcile_action=reconcile_action,
            )

        consumed_stream_events = 0
        if startup_stream_max_events is None or startup_stream_max_events > 0:
            consumed_stream_events = self._live_runner.consume_user_stream(
                event_source=event_source,
                keepalive_every=keepalive_every,
                max_events=startup_stream_max_events,
                retry_attempts=startup_stream_retry_attempts,
                retry_backoff_seconds=startup_stream_retry_backoff_seconds,
                retry_backoff_multiplier=startup_stream_retry_backoff_multiplier,
            )
        cycle_results = [
            self._live_runner.run_cycle(
                rows=item.rows,
                snapshot=item.snapshot,
                current_drawdown=item.current_drawdown,
            )
            for item in cycle_inputs
        ]
        self._logger.log(
            level="INFO",
            message="live startup completed",
            event="live.started",
            context={"consumed_stream_events": consumed_stream_events},
        )
        return LiveStartResult(
            status="started",
            preflight_report=preflight_report,
            consumed_stream_events=consumed_stream_events,
            cycle_results=cycle_results,
        )
