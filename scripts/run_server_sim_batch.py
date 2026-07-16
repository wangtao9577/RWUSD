import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import threading

from src.app.bootstrap import build_live_sim_runtime, load_settings_from_env
from src.app.simulation_snapshot import write_account_market_snapshot
from src.exchange.binance_account import BinanceAccountService
from src.exchange.binance_market import BinanceMarketDataService
from src.exchange.binance_rest import BinanceRestClient
from src.infra.live_sim_comparison import write_live_sim_comparison_report
from src.infra.simulation_batch_report import write_batch_performance_report, write_observation_archive
from src.infra.simulation_report import write_runtime_summary

_SIM_BATCH_OUTPUTS: dict[str, str] | None = None
_RUNNER_STATE_FILENAME = "runner-state.json"
_RUNNER_HEARTBEAT_INTERVAL_SECONDS = 30.0
_REQUIRED_BATCH_RELATIVE_PATHS = (
    "live-runtime-summary.json",
    "simulation/live_sim_runtime.jsonl",
    "simulation/runtime-summary.json",
    "simulation/account-market-snapshot.json",
    "simulation/simulation-outcome.json",
    "live-vs-sim-comparison.json",
)


def cli(argv: list[str] | None = None, now_fn=None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="run_server_sim_batch")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--live-log-path", required=True)
    parser.add_argument("--output-root", default="tmp/server/sim_batches")
    parser.add_argument("--batch-dir", default=None)
    parser.add_argument("--max-loops", type=int, default=None)
    parser.add_argument("--skip-live-snapshot", action="store_true")
    parser.add_argument("--skip-sim-preflight", action="store_true")
    args = parser.parse_args(argv)

    batch_dir = _resolve_batch_dir(
        args.output_root,
        now_fn=now_fn,
        batch_dir=args.batch_dir,
    )
    live_summary_path = batch_dir / "live-runtime-summary.json"
    live_snapshot_path = batch_dir / "live-account-market-snapshot.json"
    sim_dir = batch_dir / "simulation"
    sim_log_path = sim_dir / "live_sim_runtime.jsonl"
    sim_summary_path = sim_dir / "runtime-summary.json"
    sim_snapshot_path = sim_dir / "account-market-snapshot.json"
    sim_outcome_path = sim_dir / "simulation-outcome.json"
    sim_report_path = sim_dir / "performance-report.json"
    comparison_output_path = batch_dir / "live-vs-sim-comparison.json"
    runner_state_path = Path(args.output_root) / _RUNNER_STATE_FILENAME
    _set_sim_batch_outputs(
        log_path=str(sim_log_path),
        summary_output_path=str(sim_summary_path),
        snapshot_output_path=str(sim_snapshot_path),
    )
    _write_runner_state(
        runner_state_path=runner_state_path,
        output_root=args.output_root,
        batch_dir=batch_dir,
        state="running",
        now_fn=now_fn,
    )
    heartbeat_stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_runner_heartbeat_loop,
        kwargs={
            "stop_event": heartbeat_stop_event,
            "runner_state_path": runner_state_path,
            "output_root": args.output_root,
            "batch_dir": batch_dir,
            "sim_log_path": sim_log_path,
            "sim_summary_path": sim_summary_path,
            "now_fn": now_fn,
        },
        daemon=True,
    )
    heartbeat_thread.start()

    terminal_state = "stopped"
    try:
        collect_live_runtime_summary(
            log_path=args.live_log_path,
            output_path=str(live_summary_path),
        )

        if not args.skip_live_snapshot:
            collect_live_account_snapshot(
                env_file=args.env_file,
                output_path=str(live_snapshot_path),
                selected_symbols=[],
            )

        sim_result = run_live_sim_batch(
            env_file=args.env_file,
            max_loops=args.max_loops,
            skip_preflight=args.skip_sim_preflight,
        )

        build_comparison_report(
            live_summary_path=str(live_summary_path),
            sim_summary_path=str(sim_summary_path),
            live_snapshot_path=None if args.skip_live_snapshot else str(live_snapshot_path),
            sim_snapshot_path=str(sim_snapshot_path),
            output_path=str(comparison_output_path),
        )
        report = write_batch_performance_report(
            output_root=args.output_root,
            batch_dir=batch_dir,
            output_path=sim_report_path,
        )
        observation = write_observation_archive(
            output_root=args.output_root,
            report=report,
        )
        latest_path = _repair_latest_pointer(
            output_root=args.output_root,
            preferred_batch_dir=batch_dir,
        )

        return {
            "batch_dir": str(batch_dir),
            "latest_path": str(latest_path),
            "live": {
                "summary_path": str(live_summary_path),
                "snapshot_path": None if args.skip_live_snapshot else str(live_snapshot_path),
            },
            "simulation": {
                "log_path": str(sim_log_path),
                "summary_path": str(sim_summary_path),
                "snapshot_path": str(sim_snapshot_path),
                "outcome_path": str(sim_outcome_path),
                "report_path": str(sim_report_path),
                "outcome": sim_result.get("outcome"),
            },
            "comparison": {
                "output_path": str(comparison_output_path),
            },
            "observation": observation,
        }
    except Exception:
        terminal_state = "error"
        raise
    finally:
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=max(1.0, _RUNNER_HEARTBEAT_INTERVAL_SECONDS))
        _write_runner_state(
            runner_state_path=runner_state_path,
            output_root=args.output_root,
            batch_dir=batch_dir,
            state=terminal_state,
            now_fn=now_fn,
        )


def _runner_heartbeat_loop(
    *,
    stop_event: threading.Event,
    runner_state_path: Path,
    output_root: str | Path,
    batch_dir: Path,
    sim_log_path: Path,
    sim_summary_path: Path,
    now_fn=None,
) -> None:
    while not stop_event.wait(_RUNNER_HEARTBEAT_INTERVAL_SECONDS):
        _write_runner_state(
            runner_state_path=runner_state_path,
            output_root=output_root,
            batch_dir=batch_dir,
            state="running",
            now_fn=now_fn,
        )
        if sim_log_path.exists():
            try:
                write_runtime_summary(
                    log_path=sim_log_path,
                    output_path=sim_summary_path,
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # A concurrent JSONL append can leave the final line incomplete briefly.
                continue


def collect_live_runtime_summary(*, log_path: str, output_path: str) -> dict[str, object]:
    return write_runtime_summary(
        log_path=log_path,
        output_path=output_path,
    )


def collect_live_account_snapshot(
    *,
    env_file: str,
    output_path: str,
    selected_symbols: list[str] | None = None,
) -> dict[str, object]:
    settings = load_settings_from_env(env_file)
    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    market_data_service = BinanceMarketDataService(rest_client)
    return write_account_market_snapshot(
        output_path=output_path,
        account_service=account_service,
        market_data_service=market_data_service,
        candidate_symbols=settings.universe.candidate_symbols,
        interval=settings.backtest.primary_bar_interval,
        selected_symbols=selected_symbols or [],
    )


def run_live_sim_batch(
    *,
    env_file: str,
    max_loops: int | None,
    skip_preflight: bool,
) -> dict[str, object]:
    if not skip_preflight:
        # Keep behavior aligned with the dedicated script, but use the builder here
        # so this batch can control the simulation output directory.
        from src.app.bootstrap import build_live_preflight

        preflight = build_live_preflight(config_path=env_file)
        preflight_result = preflight()
    else:
        preflight_result = None

    outputs = _require_sim_batch_outputs()
    runtime = build_live_sim_runtime(
        config_path=env_file,
        log_path=outputs["log_path"],
        snapshot_output_path=outputs["snapshot_output_path"],
        summary_output_path=outputs["summary_output_path"],
    )
    runtime_result = runtime(max_loops=max_loops)
    return {
        "preflight": preflight_result,
        "runtime": runtime_result,
    }


def build_comparison_report(
    *,
    live_summary_path: str,
    sim_summary_path: str,
    output_path: str,
    live_snapshot_path: str | None = None,
    sim_snapshot_path: str | None = None,
) -> dict[str, object]:
    return write_live_sim_comparison_report(
        live_summary_path=live_summary_path,
        sim_summary_path=sim_summary_path,
        output_path=output_path,
        live_snapshot_path=live_snapshot_path,
        sim_snapshot_path=sim_snapshot_path,
    )


def _resolve_batch_dir(
    output_root: str | Path,
    now_fn=None,
    batch_dir: str | Path | None = None,
) -> Path:
    output_root_path = Path(output_root)
    if batch_dir is not None:
        candidate = Path(batch_dir)
        if not candidate.is_absolute():
            candidate = output_root_path / candidate
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    stamp = (now_fn or datetime.now)()
    resolved_batch_dir = output_root_path / stamp.strftime("%Y-%m-%d") / stamp.strftime("%H%M%S")
    resolved_batch_dir.mkdir(parents=True, exist_ok=True)
    return resolved_batch_dir


def _runner_timestamp(now_fn=None) -> str:
    stamp = (now_fn or datetime.now)()
    return stamp.isoformat()


def _write_runner_state(
    *,
    runner_state_path: Path,
    output_root: str | Path,
    batch_dir: Path,
    state: str,
    now_fn=None,
) -> None:
    output_root_path = Path(output_root)
    payload = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": _runner_timestamp(now_fn=now_fn),
        "batch_dir": _relative_to_output_root(output_root_path, batch_dir),
    }
    _atomic_write_json(runner_state_path, payload)


def _set_sim_batch_outputs(
    *,
    log_path: str,
    summary_output_path: str,
    snapshot_output_path: str,
) -> None:
    global _SIM_BATCH_OUTPUTS
    _SIM_BATCH_OUTPUTS = {
        "log_path": log_path,
        "summary_output_path": summary_output_path,
        "snapshot_output_path": snapshot_output_path,
    }


def _require_sim_batch_outputs() -> dict[str, str]:
    if _SIM_BATCH_OUTPUTS is None:
        raise RuntimeError("simulation batch outputs are not configured")
    return _SIM_BATCH_OUTPUTS


def _write_latest_pointer(
    *,
    output_root: str | Path,
    batch_dir: Path,
    live_summary_path: Path,
    live_snapshot_path: Path | None,
    sim_log_path: Path,
    sim_summary_path: Path,
    sim_snapshot_path: Path,
    sim_outcome_path: Path,
    comparison_output_path: Path,
) -> Path:
    output_root_path = Path(output_root)
    latest_path = output_root_path / "latest.json"
    payload = _build_latest_payload(
        output_root=output_root_path,
        batch_dir=batch_dir,
        live_summary_path=live_summary_path,
        live_snapshot_path=live_snapshot_path,
        sim_log_path=sim_log_path,
        sim_summary_path=sim_summary_path,
        sim_snapshot_path=sim_snapshot_path,
        sim_outcome_path=sim_outcome_path,
        comparison_output_path=comparison_output_path,
    )
    _atomic_write_json(latest_path, payload)
    return latest_path


def _relative_to_output_root(output_root: Path, target_path: Path) -> str:
    return target_path.relative_to(output_root).as_posix()


def _build_latest_payload(
    *,
    output_root: Path,
    batch_dir: Path,
    live_summary_path: Path,
    live_snapshot_path: Path | None,
    sim_log_path: Path,
    sim_summary_path: Path,
    sim_snapshot_path: Path,
    sim_outcome_path: Path,
    sim_report_path: Path | None,
    comparison_output_path: Path,
) -> dict[str, object]:
    return {
        "batch_dir": _relative_to_output_root(output_root, batch_dir),
        "live": {
            "summary_path": _relative_to_output_root(output_root, live_summary_path),
            "snapshot_path": None
            if live_snapshot_path is None
            else _relative_to_output_root(output_root, live_snapshot_path),
        },
        "simulation": {
            "log_path": _relative_to_output_root(output_root, sim_log_path),
            "summary_path": _relative_to_output_root(output_root, sim_summary_path),
            "snapshot_path": _relative_to_output_root(output_root, sim_snapshot_path),
            "outcome_path": _relative_to_output_root(output_root, sim_outcome_path),
            "report_path": None
            if sim_report_path is None
            else _relative_to_output_root(output_root, sim_report_path),
        },
        "comparison": {
            "output_path": _relative_to_output_root(output_root, comparison_output_path),
        },
    }


def _atomic_write_json(target_path: Path, payload: dict[str, object]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, target_path)


def _repair_latest_pointer(
    *,
    output_root: str | Path,
    preferred_batch_dir: Path | None = None,
) -> Path:
    output_root_path = Path(output_root)
    latest_path = output_root_path / "latest.json"
    candidate_batch = _select_latest_complete_batch(
        output_root=output_root_path,
        preferred_batch_dir=preferred_batch_dir,
    )
    if candidate_batch is None:
        raise RuntimeError("no complete simulation batch found for latest.json repair")

    payload = _build_latest_payload_from_batch(
        output_root=output_root_path,
        batch_dir=candidate_batch,
    )
    _atomic_write_json(latest_path, payload)
    return latest_path


def _select_latest_complete_batch(
    *,
    output_root: Path,
    preferred_batch_dir: Path | None = None,
) -> Path | None:
    candidates: list[Path] = []
    if preferred_batch_dir is not None and _is_complete_batch_dir(preferred_batch_dir):
        candidates.append(preferred_batch_dir)

    if output_root.exists():
        for date_dir in output_root.iterdir():
            if not date_dir.is_dir():
                continue
            for batch_dir in date_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                if _is_complete_batch_dir(batch_dir):
                    candidates.append(batch_dir)

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda path: path.relative_to(output_root).as_posix(),
    )


def _is_complete_batch_dir(batch_dir: Path) -> bool:
    return all((batch_dir / relative_path).exists() for relative_path in _REQUIRED_BATCH_RELATIVE_PATHS)


def _build_latest_payload_from_batch(
    *,
    output_root: Path,
    batch_dir: Path,
) -> dict[str, object]:
    live_summary_path = batch_dir / "live-runtime-summary.json"
    live_snapshot_path = batch_dir / "live-account-market-snapshot.json"
    sim_dir = batch_dir / "simulation"
    sim_log_path = sim_dir / "live_sim_runtime.jsonl"
    sim_summary_path = sim_dir / "runtime-summary.json"
    sim_snapshot_path = sim_dir / "account-market-snapshot.json"
    sim_outcome_path = sim_dir / "simulation-outcome.json"
    comparison_output_path = batch_dir / "live-vs-sim-comparison.json"
    return _build_latest_payload(
        output_root=output_root,
        batch_dir=batch_dir,
        live_summary_path=live_summary_path,
        live_snapshot_path=live_snapshot_path if live_snapshot_path.exists() else None,
        sim_log_path=sim_log_path,
        sim_summary_path=sim_summary_path,
        sim_snapshot_path=sim_snapshot_path,
        sim_outcome_path=sim_outcome_path,
        sim_report_path=(batch_dir / "simulation" / "performance-report.json")
        if (batch_dir / "simulation" / "performance-report.json").exists()
        else None,
        comparison_output_path=comparison_output_path,
    )


if __name__ == "__main__":
    cli()
