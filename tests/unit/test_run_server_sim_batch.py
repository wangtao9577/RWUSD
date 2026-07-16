import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
import time
from unittest.mock import patch


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_server_sim_batch.py"
    spec = importlib.util.spec_from_file_location("run_server_sim_batch_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_complete_batch_outputs(
    module,
    *,
    live_summary_path: str | Path | None = None,
    live_snapshot_path: str | Path | None = None,
    comparison_output_path: str | Path | None = None,
) -> None:
    outputs = module._require_sim_batch_outputs()
    sim_log_path = Path(outputs["log_path"])
    sim_summary_path = Path(outputs["summary_output_path"])
    sim_snapshot_path = Path(outputs["snapshot_output_path"])
    sim_outcome_path = sim_summary_path.parent / "simulation-outcome.json"

    sim_log_path.parent.mkdir(parents=True, exist_ok=True)
    sim_log_path.write_text('{"event":"tick"}\n', encoding="utf-8")
    _write_json(
        sim_summary_path,
        {
            "status": "ok",
            "loop_completed_count": 1,
            "profit_sweep_count": 0,
            "redeem_topup_count": 0,
            "rwusd_principal": "10000",
            "rwusd_interest_accrued": "0",
        },
    )
    _write_json(
        sim_snapshot_path,
        {
            "selected_symbols": ["SOLUSDT"],
            "strategy": {"phase": "HEDGED", "take_profit_count": 0, "restore_count": 0},
        },
    )
    _write_json(
        sim_outcome_path,
        {
            "verdict": "pass",
            "initial_capital_usdt": "10000",
            "rwusd_principal": "10000",
            "rwusd_interest_accrued": "0",
        },
    )
    _write_json(
        sim_summary_path.parent / "performance-report.json",
        {
            "current_batch": {"batch_dir": "2026-06-29/134512"},
            "pdf_alignment": {"completion_pct": 88},
        },
    )

    if live_summary_path is not None:
        _write_json(Path(live_summary_path), {"loop_completed_count": 1})
    if live_snapshot_path is not None:
        _write_json(Path(live_snapshot_path), {"account": {"account_equity": "10000"}})
    if comparison_output_path is not None:
        _write_json(Path(comparison_output_path), {"comparison_status": "ok"})


def _create_complete_batch(root: Path, batch_rel: str, *, with_live_snapshot: bool = True) -> Path:
    batch_dir = root / batch_rel
    _write_json(batch_dir / "live-runtime-summary.json", {"loop_completed_count": 1})
    if with_live_snapshot:
        _write_json(batch_dir / "live-account-market-snapshot.json", {"account": {"account_equity": "10000"}})
    _write_json(batch_dir / "live-vs-sim-comparison.json", {"comparison_status": "ok"})
    _write_json(batch_dir / "simulation" / "runtime-summary.json", {"status": "ok"})
    _write_json(batch_dir / "simulation" / "account-market-snapshot.json", {"strategy": {"phase": "HEDGED"}})
    _write_json(batch_dir / "simulation" / "simulation-outcome.json", {"verdict": "pass"})
    _write_json(
        batch_dir / "simulation" / "performance-report.json",
        {
            "current_batch": {"batch_dir": batch_rel},
            "pdf_alignment": {"completion_pct": 88},
        },
    )
    (batch_dir / "simulation" / "live_sim_runtime.jsonl").write_text('{"event":"tick"}\n', encoding="utf-8")
    return batch_dir


class RunServerSimBatchScriptTests(unittest.TestCase):
    def test_cli_writes_runner_state_file_for_running_and_terminal_states(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            fixed_now = datetime(2026, 6, 29, 13, 45, 12)
            observed_running_state: dict[str, object] = {}

            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
                _write_json(Path(output_path), {"env_file": env_file, "selected_symbols": selected_symbols or []})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                runner_state_path = base_dir / "runner-state.json"
                observed_running_state.update(json.loads(runner_state_path.read_text(encoding="utf-8")))
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": [{"selected_symbol": "SOLUSDT"}]},
                    "outcome": {"verdict": "pass"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            with patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                    ],
                    now_fn=lambda: fixed_now,
                )

            final_runner_state = json.loads((base_dir / "runner-state.json").read_text(encoding="utf-8"))

        self.assertEqual(observed_running_state["state"], "running")
        self.assertEqual(observed_running_state["batch_dir"], "2026-06-29/134512")
        self.assertTrue(observed_running_state["updated_at"])
        self.assertTrue(int(observed_running_state["pid"]) > 0)
        self.assertEqual(final_runner_state["state"], "stopped")
        self.assertEqual(final_runner_state["batch_dir"], "2026-06-29/134512")
        self.assertTrue(final_runner_state["updated_at"])

    def test_cli_refreshes_runner_state_while_simulation_is_running(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            fixed_now = datetime(2026, 6, 29, 13, 45, 12)
            recorded_states: list[tuple[str, str]] = []
            running_summary_calls: list[tuple[str, str]] = []
            original_write_runner_state = module._write_runner_state

            def wrapped_write_runner_state(**kwargs):
                original_write_runner_state(**kwargs)
                payload = json.loads(Path(kwargs["runner_state_path"]).read_text(encoding="utf-8"))
                recorded_states.append((str(payload["state"]), str(payload["updated_at"])))

            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
                _write_json(Path(output_path), {"env_file": env_file, "selected_symbols": selected_symbols or []})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                runtime_log_path = (
                    base_dir
                    / "2026-06-29"
                    / "134512"
                    / "simulation"
                    / "live_sim_runtime.jsonl"
                )
                runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
                runtime_log_path.write_text(
                    '{"event":"runtime.loop_completed","context":{"loop_count":1}}\n',
                    encoding="utf-8",
                )
                time.sleep(0.08)
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": [{"selected_symbol": "SOLUSDT"}]},
                    "outcome": {"verdict": "pass"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            def fake_write_runtime_summary(*, log_path, output_path):
                running_summary_calls.append((str(log_path), str(output_path)))
                _write_json(Path(output_path), {"loop_completed_count": 1})
                return {"loop_completed_count": 1}

            with patch.object(module, "_RUNNER_HEARTBEAT_INTERVAL_SECONDS", 0.01), patch.object(
                module,
                "_write_runner_state",
                side_effect=wrapped_write_runner_state,
            ), patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ), patch.object(
                module,
                "write_runtime_summary",
                side_effect=fake_write_runtime_summary,
            ):
                module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                    ],
                    now_fn=lambda: fixed_now,
                )

        running_writes = [state for state, _ in recorded_states if state == "running"]
        self.assertGreaterEqual(len(running_writes), 2)
        self.assertIn(
            (
                str(
                    base_dir
                    / "2026-06-29"
                    / "134512"
                    / "simulation"
                    / "live_sim_runtime.jsonl"
                ),
                str(
                    base_dir
                    / "2026-06-29"
                    / "134512"
                    / "simulation"
                    / "runtime-summary.json"
                ),
            ),
            running_summary_calls,
        )

    def test_cli_runs_live_collect_sim_and_comparison_in_order(self) -> None:
        module = _load_script_module()
        call_order: list[str] = []

        def fake_collect_live_summary(*, log_path, output_path):
            call_order.append("live_summary")
            _write_json(Path(output_path), {"log_path": log_path})
            return {"log_path": log_path, "output_path": output_path}

        def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
            call_order.append("live_snapshot")
            _write_json(Path(output_path), {"env_file": env_file})
            return {"output_path": output_path, "selected_symbols": selected_symbols or []}

        def fake_run_sim(*, env_file, max_loops, skip_preflight):
            call_order.append("sim_runtime")
            _write_complete_batch_outputs(module)
            return {
                "outcome": {"verdict": "pass"},
                "runtime": {
                    "status": "started",
                    "loop_results": [
                        {"selected_symbol": "ETHUSDT"},
                        {"selected_symbol": "ETHUSDT"},
                    ],
                }
            }

        def fake_compare(**kwargs):
            call_order.append("comparison")
            _write_json(Path(kwargs["output_path"]), {"status": "ok"})
            return kwargs

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            fixed_now = datetime(2026, 6, 29, 13, 45, 12)
            with patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                        "--max-loops",
                        "25",
                    ],
                    now_fn=lambda: fixed_now,
                )

        batch_dir = base_dir / "2026-06-29" / "134512"
        self.assertEqual(call_order, ["live_summary", "live_snapshot", "sim_runtime", "comparison"])
        self.assertEqual(result["batch_dir"], str(batch_dir))
        self.assertEqual(result["latest_path"], str(base_dir / "latest.json"))
        self.assertEqual(result["live"]["summary_path"], str(batch_dir / "live-runtime-summary.json"))
        self.assertEqual(result["live"]["snapshot_path"], str(batch_dir / "live-account-market-snapshot.json"))
        self.assertEqual(result["simulation"]["summary_path"], str(batch_dir / "simulation" / "runtime-summary.json"))
        self.assertEqual(result["simulation"]["snapshot_path"], str(batch_dir / "simulation" / "account-market-snapshot.json"))
        self.assertEqual(result["simulation"]["outcome_path"], str(batch_dir / "simulation" / "simulation-outcome.json"))
        self.assertEqual(result["simulation"]["report_path"], str(batch_dir / "simulation" / "performance-report.json"))
        self.assertEqual(result["simulation"]["outcome"]["verdict"], "pass")
        self.assertEqual(result["comparison"]["output_path"], str(batch_dir / "live-vs-sim-comparison.json"))

    def test_cli_writes_latest_pointer_file(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            fixed_now = datetime(2026, 6, 29, 13, 45, 12)
            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
                _write_json(Path(output_path), {"env_file": env_file, "selected_symbols": selected_symbols or []})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": []},
                    "outcome": {"verdict": "borderline"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            with patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                    ],
                    now_fn=lambda: fixed_now,
                )

            latest_path = base_dir / "latest.json"
            self.assertTrue(latest_path.exists())
            payload = latest_path.read_text(encoding="utf-8")
            self.assertIn("2026-06-29/134512", payload)
            self.assertIn("simulation/simulation-outcome.json", payload)
            self.assertIn("simulation/performance-report.json", payload)
            self.assertEqual(result["latest_path"], str(latest_path))

    def test_cli_writes_observation_archive_files(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)

            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
                _write_json(Path(output_path), {"env_file": env_file, "selected_symbols": selected_symbols or []})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": [{"selected_symbol": "SOLUSDT"}]},
                    "outcome": {"verdict": "pass"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            with patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                    ],
                    now_fn=lambda: datetime(2026, 6, 29, 13, 45, 12),
                )

            latest_observation_path = base_dir / "observation" / "latest.json"
            history_observation_path = base_dir / "observation" / "history.json"
            experiment_summary_path = base_dir / "observation" / "experiment-summary.json"

            latest_payload = json.loads(latest_observation_path.read_text(encoding="utf-8"))
            history_payload = json.loads(history_observation_path.read_text(encoding="utf-8"))
            experiment_summary_payload = json.loads(experiment_summary_path.read_text(encoding="utf-8"))

        self.assertEqual(result["observation"]["latest_path"], str(latest_observation_path))
        self.assertEqual(result["observation"]["history_path"], str(history_observation_path))
        self.assertEqual(result["observation"]["experiment_summary_path"], str(experiment_summary_path))
        self.assertEqual(latest_payload["batch_dir"], "2026-06-29/134512")
        self.assertEqual(history_payload["entries"][0]["batch_dir"], "2026-06-29/134512")
        self.assertEqual(history_payload["entries"][0]["verdict"], "pass")
        self.assertEqual(len(history_payload["entries"]), 1)
        self.assertEqual(experiment_summary_payload["batch_count"], 1)
        self.assertEqual(experiment_summary_payload["pass_batch_count"], 1)
        self.assertEqual(experiment_summary_payload["selected_symbol_counts"]["SOLUSDT"], 1)

    def test_cli_can_skip_live_snapshot_preflight_for_sim(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": []},
                    "outcome": {"verdict": "pass"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            with patch.object(module, "collect_live_runtime_summary", return_value={}), patch.object(
                module,
                "collect_live_runtime_summary",
                side_effect=fake_collect_live_summary,
            ), patch.object(
                module,
                "collect_live_account_snapshot",
            ) as mocked_live_snapshot, patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ) as mocked_run_sim, patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                        "--skip-live-snapshot",
                        "--skip-sim-preflight",
                    ],
                    now_fn=lambda: datetime(2026, 6, 29, 13, 45, 12),
                )

        mocked_live_snapshot.assert_not_called()
        mocked_run_sim.assert_called_once_with(
            env_file="server.env",
            max_loops=None,
            skip_preflight=True,
        )

    def test_cli_reuses_explicit_batch_dir_for_resumed_run(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            resume_dir = base_dir / "2026-07-04" / "152046"
            observed_running_state: dict[str, object] = {}

            def fake_collect_live_summary(*, log_path, output_path):
                _write_json(Path(output_path), {"log_path": log_path})
                return {}

            def fake_collect_live_snapshot(*, output_path, env_file, selected_symbols=None):
                _write_json(Path(output_path), {"env_file": env_file, "selected_symbols": selected_symbols or []})
                return {}

            def fake_run_sim(*, env_file, max_loops, skip_preflight):
                runner_state_path = base_dir / "runner-state.json"
                observed_running_state.update(json.loads(runner_state_path.read_text(encoding="utf-8")))
                _write_complete_batch_outputs(module)
                return {
                    "runtime": {"status": "started", "loop_results": [{"selected_symbol": "SOLUSDT"}]},
                    "outcome": {"verdict": "pass"},
                }

            def fake_compare(**kwargs):
                _write_json(Path(kwargs["output_path"]), {"status": "ok"})
                return {}

            with patch.object(module, "collect_live_runtime_summary", side_effect=fake_collect_live_summary), patch.object(
                module,
                "collect_live_account_snapshot",
                side_effect=fake_collect_live_snapshot,
            ), patch.object(
                module,
                "run_live_sim_batch",
                side_effect=fake_run_sim,
            ), patch.object(
                module,
                "build_comparison_report",
                side_effect=fake_compare,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(base_dir),
                        "--batch-dir",
                        "2026-07-04/152046",
                    ],
                    now_fn=lambda: datetime(2026, 7, 4, 16, 0, 0),
                )

        self.assertEqual(observed_running_state["batch_dir"], "2026-07-04/152046")
        self.assertEqual(result["batch_dir"], str(resume_dir))

    def test_repair_latest_pointer_prefers_latest_complete_batch_over_stale_latest_json(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            old_batch = _create_complete_batch(base_dir, "2026-07-01/212048")
            new_batch = _create_complete_batch(base_dir, "2026-07-01/213535", with_live_snapshot=False)
            _write_json(
                base_dir / "latest.json",
                {
                    "batch_dir": old_batch.relative_to(base_dir).as_posix(),
                    "simulation": {
                        "summary_path": "2026-07-01/212048/simulation/runtime-summary.json",
                    },
                },
            )

            latest_path = module._repair_latest_pointer(output_root=base_dir)

            payload = json.loads(latest_path.read_text(encoding="utf-8"))

        self.assertEqual(latest_path, base_dir / "latest.json")
        self.assertEqual(payload["batch_dir"], new_batch.relative_to(base_dir).as_posix())
        self.assertIsNone(payload["live"]["snapshot_path"])
        self.assertEqual(
            payload["simulation"]["outcome_path"],
            "2026-07-01/213535/simulation/simulation-outcome.json",
        )

    def test_atomic_write_json_replaces_target_without_leaving_tmp_file(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            latest_path = Path(tmp_dir) / "latest.json"
            latest_path.write_text('{"batch_dir":"old"}', encoding="utf-8")

            module._atomic_write_json(latest_path, {"batch_dir": "2026-07-01/213535"})

            payload = json.loads(latest_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["batch_dir"], "2026-07-01/213535")
        self.assertFalse((latest_path.parent / "latest.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
