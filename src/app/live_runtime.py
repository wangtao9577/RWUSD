import json
import time
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from src.app.live_orchestrator import LiveCycleInput
from src.infra.alerts import InMemoryAlertSink
from src.infra.logging import InMemoryLogger


ZERO = Decimal("0")
SECONDS_PER_HOUR = Decimal("3600")


@dataclass(slots=True)
class LiveRuntimeResult:
    status: str
    startup_result: object
    loop_count: int = 0
    loop_results: list[object] = field(default_factory=list)
    outcome: dict[str, object] | None = None


class FileCycleInputProvider:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._index = 0
        self._batches = self._load_batches()

    def __call__(self) -> list[LiveCycleInput]:
        if self._index >= len(self._batches):
            return []

        batch = self._batches[self._index]
        self._index += 1
        return batch

    def _load_batches(self) -> list[list[LiveCycleInput]]:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        batches: list[list[LiveCycleInput]] = []
        for raw_batch in payload:
            parsed_batch: list[LiveCycleInput] = []
            for item in raw_batch:
                parsed_batch.append(
                    LiveCycleInput(
                        rows=item.get("rows", []),
                        snapshot=item.get("snapshot"),
                        current_drawdown=Decimal(str(item.get("current_drawdown", "0"))),
                    )
                )
            batches.append(parsed_batch)
        return batches


class LiveRuntime:
    def __init__(
        self,
        startup,
        live_runner,
        event_source,
        cycle_input_provider,
        poll_interval_seconds: float = 5.0,
        sleep_fn=None,
        logger=None,
        alert_sink=None,
        thread_factory=None,
        enable_background_user_stream: bool = True,
    ) -> None:
        self._startup = startup
        self._live_runner = live_runner
        self._event_source = event_source
        self._cycle_input_provider = cycle_input_provider
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep_fn = sleep_fn or time.sleep
        self._logger = logger or InMemoryLogger()
        self._alert_sink = alert_sink or InMemoryAlertSink()
        self._thread_factory = thread_factory or threading.Thread
        self._enable_background_user_stream = enable_background_user_stream
        self._default_cycle_retry_attempts = 0
        self._default_cycle_retry_backoff_seconds = 1.0
        self._default_cycle_retry_backoff_multiplier = 2.0
        self._default_stream_retry_attempts = 0
        self._default_stream_retry_backoff_seconds = 1.0
        self._default_stream_retry_backoff_multiplier = 2.0

    def __call__(
        self,
        max_loops: int | None = None,
        keepalive_every: int = 50,
        startup_stream_max_events: int | None = 0,
        cycle_retry_attempts: int | None = None,
        cycle_retry_backoff_seconds: float | None = None,
        cycle_retry_backoff_multiplier: float | None = None,
    ) -> LiveRuntimeResult:
        effective_cycle_retry_attempts = (
            self._default_cycle_retry_attempts
            if cycle_retry_attempts is None
            else cycle_retry_attempts
        )
        effective_cycle_retry_backoff_seconds = (
            self._default_cycle_retry_backoff_seconds
            if cycle_retry_backoff_seconds is None
            else cycle_retry_backoff_seconds
        )
        effective_cycle_retry_backoff_multiplier = (
            self._default_cycle_retry_backoff_multiplier
            if cycle_retry_backoff_multiplier is None
            else cycle_retry_backoff_multiplier
        )
        self._logger.log(
            level="INFO",
            message="runtime startup started",
            event="runtime.startup_started",
        )
        startup_result = self._startup(
            event_source=self._event_source,
            cycle_inputs=[],
            keepalive_every=keepalive_every,
            startup_stream_max_events=startup_stream_max_events,
            startup_stream_retry_attempts=self._default_stream_retry_attempts,
            startup_stream_retry_backoff_seconds=self._default_stream_retry_backoff_seconds,
            startup_stream_retry_backoff_multiplier=self._default_stream_retry_backoff_multiplier,
        )
        if startup_result.status != "started":
            self._logger.log(
                level="ERROR",
                message="runtime startup failed",
                event="runtime.startup_failed",
                context={"status": startup_result.status},
            )
            self._alert_sink.send(channel="runtime", body=startup_result.status)
            return LiveRuntimeResult(
                status=startup_result.status,
                startup_result=startup_result,
            )
        self._logger.log(
            level="INFO",
            message="runtime startup completed",
            event="runtime.startup_completed",
        )
        self._start_background_user_stream(keepalive_every=keepalive_every)

        loop_results: list[object] = []
        loop_count = 0
        initial_profit_bucket = self._profit_bucket_snapshot()
        if self._is_resumed_runtime_state():
            previous_profit_sweep_count = self._extract_profit_bucket_count(
                initial_profit_bucket,
                "deposit_count",
            )
            previous_redeem_topup_count = self._extract_profit_bucket_count(
                initial_profit_bucket,
                "redeem_count",
            )
        else:
            previous_profit_sweep_count = 0
            previous_redeem_topup_count = 0
        while max_loops is None or loop_count < max_loops:
            attempts_remaining = effective_cycle_retry_attempts
            current_retry_backoff = effective_cycle_retry_backoff_seconds
            while True:
                try:
                    cycle_inputs = self._cycle_input_provider()
                    break
                except Exception as exc:
                    self._logger.log(
                        level="WARN",
                        message="runtime cycle input provider failed",
                        event="runtime.loop_retry",
                        context={
                            "error": str(exc),
                            "attempts_remaining": attempts_remaining,
                            "backoff_seconds": current_retry_backoff,
                        },
                    )
                    self._alert_sink.send(channel="runtime", body="cycle_input_retry")
                    if attempts_remaining <= 0:
                        raise
                    attempts_remaining -= 1
                    if current_retry_backoff > 0:
                        self._sleep_fn(current_retry_backoff)
                    current_retry_backoff *= effective_cycle_retry_backoff_multiplier

            current_loop_results: list[object] = []
            for item in cycle_inputs:
                elapsed_hours = ZERO
                if loop_count > 0 and self._poll_interval_seconds > 0:
                    elapsed_hours = Decimal(str(self._poll_interval_seconds)) / SECONDS_PER_HOUR
                result = self._live_runner.run_cycle(
                    rows=item.rows,
                    snapshot=item.snapshot,
                    current_drawdown=item.current_drawdown,
                    elapsed_hours=elapsed_hours,
                )
                current_loop_results.append(result)
                loop_results.append(result)

            loop_count += 1
            profit_bucket = self._profit_bucket_snapshot()
            profit_sweep_total = self._extract_profit_bucket_count(
                profit_bucket,
                "deposit_count",
            )
            redeem_topup_total = self._extract_profit_bucket_count(
                profit_bucket,
                "redeem_count",
            )
            self._logger.log(
                level="INFO",
                message="runtime loop completed",
                event="runtime.loop_completed",
                context={
                    "loop_count": loop_count,
                    "selected_symbols": self._extract_selected_symbols(
                        current_loop_results
                    ),
                    "intent_actions": [
                        self._extract_intent_action(result)
                        for result in current_loop_results
                    ],
                    "risk_reasons": [
                        self._extract_field(result, "risk_reason")
                        for result in current_loop_results
                    ],
                    "rebalance_actions": [
                        self._extract_rebalance_action(result)
                        for result in current_loop_results
                    ],
                    "profit_sweep_count": max(
                        0,
                        profit_sweep_total - previous_profit_sweep_count,
                    ),
                    "redeem_topup_count": max(
                        0,
                        redeem_topup_total - previous_redeem_topup_count,
                    ),
                    "rwusd_principal": self._extract_profit_bucket_metric(
                        profit_bucket,
                        "rwusd_principal",
                    ),
                    "rwusd_interest_accrued": self._extract_profit_bucket_metric(
                        profit_bucket,
                        "rwusd_interest_accrued",
                    ),
                    "harvest_buffer": self._extract_profit_bucket_metric(
                        profit_bucket,
                        "harvest_buffer",
                    ),
                    "closed_loop_ready": bool(
                        self._extract_field(profit_bucket, "closed_loop_ready")
                    ),
                    "last_rebalance_action": self._extract_field(
                        profit_bucket,
                        "last_rebalance_action",
                    ),
                    "sweep_block_reason": self._extract_field(
                        profit_bucket,
                        "sweep_block_reason",
                    ),
                },
            )
            previous_profit_sweep_count = profit_sweep_total
            previous_redeem_topup_count = redeem_topup_total
            if max_loops is not None and loop_count >= max_loops:
                break
            self._sleep_fn(self._poll_interval_seconds)

        return LiveRuntimeResult(
            status="started",
            startup_result=startup_result,
            loop_count=loop_count,
            loop_results=loop_results,
        )

    def set_retry_defaults(
        self,
        cycle_retry_attempts: int,
        cycle_retry_backoff_seconds: float,
        cycle_retry_backoff_multiplier: float,
        stream_retry_attempts: int,
        stream_retry_backoff_seconds: float,
        stream_retry_backoff_multiplier: float,
    ) -> None:
        self._default_cycle_retry_attempts = cycle_retry_attempts
        self._default_cycle_retry_backoff_seconds = cycle_retry_backoff_seconds
        self._default_cycle_retry_backoff_multiplier = cycle_retry_backoff_multiplier
        self._default_stream_retry_attempts = stream_retry_attempts
        self._default_stream_retry_backoff_seconds = stream_retry_backoff_seconds
        self._default_stream_retry_backoff_multiplier = stream_retry_backoff_multiplier

    def _start_background_user_stream(self, keepalive_every: int) -> None:
        if not self._enable_background_user_stream:
            return

        def run_user_stream() -> None:
            try:
                self._live_runner.consume_user_stream(
                    event_source=self._event_source,
                    keepalive_every=keepalive_every,
                    max_events=None,
                    retry_attempts=self._default_stream_retry_attempts,
                    retry_backoff_seconds=self._default_stream_retry_backoff_seconds,
                    retry_backoff_multiplier=self._default_stream_retry_backoff_multiplier,
                )
            except Exception as exc:
                self._logger.log(
                    level="ERROR",
                    message="background user stream failed",
                    event="runtime.user_stream_failed",
                    context={"error": str(exc)},
                )
                self._alert_sink.send(channel="runtime", body="user_stream_failed")

        worker = self._thread_factory(
            target=run_user_stream,
            daemon=True,
        )
        worker.start()

    def _extract_field(self, result: object, field_name: str):
        if hasattr(result, field_name):
            return getattr(result, field_name)
        if isinstance(result, dict):
            return result.get(field_name)
        return None

    def _extract_intent_action(self, result: object):
        intent = self._extract_field(result, "intent")
        if intent is None:
            return None
        if hasattr(intent, "action"):
            return getattr(intent, "action")
        if isinstance(intent, dict):
            return intent.get("action")
        return None

    def _extract_rebalance_action(self, result: object):
        rebalance_action = self._extract_field(result, "rebalance_action")
        if rebalance_action is not None:
            return rebalance_action
        return self._extract_field(result, "rebalance_decision_action")

    def _extract_selected_symbols(self, results: list[object]) -> list[object]:
        selected_symbols: list[object] = []
        for result in results:
            many = self._extract_field(result, "selected_symbols")
            if isinstance(many, list):
                selected_symbols.extend(
                    symbol for symbol in many if symbol is not None
                )
                continue
            single = self._extract_field(result, "selected_symbol")
            if single is not None:
                selected_symbols.append(single)
        return selected_symbols

    def _profit_bucket_snapshot(self):
        return getattr(self._live_runner, "profit_bucket", None)

    def _is_resumed_runtime_state(self) -> bool:
        return bool(getattr(self._live_runner, "restored_runtime_state", False))

    def _extract_profit_bucket_count(self, bucket: object, field_name: str) -> int:
        if bucket is None or not hasattr(bucket, field_name):
            return 0
        return int(getattr(bucket, field_name) or 0)

    def _extract_profit_bucket_metric(self, bucket: object, field_name: str) -> str:
        if bucket is None or not hasattr(bucket, field_name):
            return "0"
        return str(getattr(bucket, field_name) or 0)
