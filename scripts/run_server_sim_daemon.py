import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


_SUPERVISOR_STATE_FILENAME = "supervisor-state.json"
_RUNNER_STATE_FILENAME = "runner-state.json"
_REQUIRED_BATCH_RELATIVE_PATHS = (
    "live-runtime-summary.json",
    "simulation/live_sim_runtime.jsonl",
    "simulation/runtime-summary.json",
    "simulation/account-market-snapshot.json",
    "simulation/simulation-outcome.json",
    "live-vs-sim-comparison.json",
)


def cli(argv: list[str] | None = None, now_fn=None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="run_server_sim_daemon")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--live-log-path", required=True)
    parser.add_argument("--output-root", default="tmp/server/sim_batches")
    parser.add_argument("--max-loops", type=int, default=None)
    parser.add_argument("--skip-live-snapshot", action="store_true")
    parser.add_argument("--skip-sim-preflight", action="store_true")
    parser.add_argument("--restart-delay-seconds", type=float, default=5.0)
    parser.add_argument("--failure-backoff-seconds", type=float, default=15.0)
    parser.add_argument("--failure-backoff-multiplier", type=float, default=2.0)
    parser.add_argument("--max-failure-backoff-seconds", type=float, default=300.0)
    parser.add_argument("--max-runs", type=int, default=None)
    args = parser.parse_args(argv)

    output_root = Path(args.output_root)
    state_path = output_root / _SUPERVISOR_STATE_FILENAME
    successful_runs = 0
    failure_count = 0
    attempt_count = 0
    last_batch_dir = ""
    last_error = ""
    current_backoff = max(0.0, float(args.failure_backoff_seconds))

    _write_supervisor_state(
        state_path=state_path,
        state="starting",
        successful_runs=successful_runs,
        failure_count=failure_count,
        attempt_count=attempt_count,
        last_batch_dir=last_batch_dir,
        last_error=last_error,
        now_fn=now_fn,
    )

    while args.max_runs is None or successful_runs < args.max_runs:
        attempt_count += 1
        _write_supervisor_state(
            state_path=state_path,
            state="running",
            successful_runs=successful_runs,
            failure_count=failure_count,
            attempt_count=attempt_count,
            last_batch_dir=last_batch_dir,
            last_error=last_error,
            now_fn=now_fn,
        )
        try:
            resumed_batch_dir = _discover_resumable_batch_dir(output_root)
            batch_argv = _build_batch_argv(args, batch_dir=resumed_batch_dir)
            result = _run_batch_once(batch_argv)
        except Exception as exc:
            failure_count += 1
            last_error = f"{type(exc).__name__}: {exc}"
            _write_supervisor_state(
                state_path=state_path,
                state="error",
                successful_runs=successful_runs,
                failure_count=failure_count,
                attempt_count=attempt_count,
                last_batch_dir=last_batch_dir,
                last_error=last_error,
                now_fn=now_fn,
            )
            time.sleep(current_backoff)
            current_backoff = min(
                current_backoff * max(float(args.failure_backoff_multiplier), 1.0),
                max(float(args.max_failure_backoff_seconds), 0.0),
            )
            continue

        last_batch_dir = str((result or {}).get("batch_dir") or last_batch_dir)
        successful_runs += 1
        current_backoff = max(0.0, float(args.failure_backoff_seconds))
        _write_supervisor_state(
            state_path=state_path,
            state="running",
            successful_runs=successful_runs,
            failure_count=failure_count,
            attempt_count=attempt_count,
            last_batch_dir=last_batch_dir,
            last_error=last_error,
            now_fn=now_fn,
        )
        if args.max_runs is not None and successful_runs >= args.max_runs:
            break
        time.sleep(max(0.0, float(args.restart_delay_seconds)))

    _write_supervisor_state(
        state_path=state_path,
        state="stopped",
        successful_runs=successful_runs,
        failure_count=failure_count,
        attempt_count=attempt_count,
        last_batch_dir=last_batch_dir,
        last_error=last_error,
        now_fn=now_fn,
    )
    return {
        "successful_runs": successful_runs,
        "failure_count": failure_count,
        "attempt_count": attempt_count,
        "last_batch_dir": last_batch_dir,
        "last_error": last_error,
        "state_path": str(state_path),
    }


def _build_batch_argv(args: argparse.Namespace, batch_dir: str | None = None) -> list[str]:
    argv = [
        "--env-file",
        str(args.env_file),
        "--live-log-path",
        str(args.live_log_path),
        "--output-root",
        str(args.output_root),
    ]
    if batch_dir:
        argv.extend(["--batch-dir", str(batch_dir)])
    if args.max_loops is not None:
        argv.extend(["--max-loops", str(args.max_loops)])
    if args.skip_live_snapshot:
        argv.append("--skip-live-snapshot")
    if args.skip_sim_preflight:
        argv.append("--skip-sim-preflight")
    return argv


def _discover_resumable_batch_dir(output_root: Path) -> str | None:
    runner_state_path = output_root / _RUNNER_STATE_FILENAME
    if not runner_state_path.exists():
        return None
    try:
        payload = json.loads(runner_state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    batch_dir = str(payload.get("batch_dir") or "").strip().replace("\\", "/").strip("/")
    if not batch_dir:
        return None

    candidate = output_root / Path(batch_dir)
    if not candidate.exists() or not candidate.is_dir():
        return None
    if _is_complete_batch_dir(candidate):
        return None
    sim_log_path = candidate / "simulation" / "live_sim_runtime.jsonl"
    state_db_path = candidate / "simulation" / "live_state.db"
    if not sim_log_path.exists() and not state_db_path.exists():
        return None
    return batch_dir


def _is_complete_batch_dir(batch_dir: Path) -> bool:
    return all((batch_dir / relative_path).exists() for relative_path in _REQUIRED_BATCH_RELATIVE_PATHS)


def _run_batch_once(argv: list[str]) -> dict[str, Any]:
    module = _load_batch_module()
    return module.cli(argv)


def _load_batch_module():
    _ensure_project_root_on_syspath()
    script_path = Path(__file__).with_name("run_server_sim_batch.py")
    spec = importlib.util.spec_from_file_location("run_server_sim_batch_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load batch script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_project_root_on_syspath() -> None:
    project_root = str(Path(__file__).resolve().parents[1])
    normalized_root = str(Path(project_root).resolve())
    for entry in sys.path:
        try:
            if str(Path(entry or ".").resolve()) == normalized_root:
                return
        except Exception:
            continue
    sys.path.insert(0, project_root)


def _write_supervisor_state(
    *,
    state_path: Path,
    state: str,
    successful_runs: int,
    failure_count: int,
    attempt_count: int,
    last_batch_dir: str,
    last_error: str,
    now_fn=None,
) -> None:
    payload = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": _timestamp(now_fn=now_fn),
        "successful_runs": int(successful_runs),
        "failure_count": int(failure_count),
        "attempt_count": int(attempt_count),
        "last_batch_dir": str(last_batch_dir or ""),
        "last_error": str(last_error or ""),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _timestamp(now_fn=None) -> str:
    stamp = (now_fn or datetime.now)()
    return stamp.isoformat()


if __name__ == "__main__":
    cli()
