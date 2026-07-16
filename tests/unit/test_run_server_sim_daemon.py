import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_server_sim_daemon.py"
    spec = importlib.util.spec_from_file_location("run_server_sim_daemon_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunServerSimDaemonScriptTests(unittest.TestCase):
    def test_load_batch_module_bootstraps_project_root_on_syspath(self) -> None:
        module = _load_script_module()
        project_root = str(Path(__file__).resolve().parents[2])

        original_sys_path = list(sys.path)
        try:
            sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(project_root).resolve()]
            loaded = module._load_batch_module()
        finally:
            sys.path[:] = original_sys_path

        self.assertTrue(hasattr(loaded, "cli"))
        self.assertEqual(Path(loaded.__file__).name, "run_server_sim_batch.py")

    def test_cli_retries_after_failure_then_continues_successful_runs(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir)
            observed_batch_argv: list[list[str]] = []
            sleep_calls: list[float] = []
            batch_results = [
                RuntimeError("temporary batch failure"),
                {"batch_dir": "2026-07-03/131319"},
                {"batch_dir": "2026-07-03/141319"},
            ]

            def fake_batch_cli(argv):
                observed_batch_argv.append(list(argv))
                result = batch_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            with patch.object(module, "_run_batch_once", side_effect=fake_batch_cli), patch.object(
                module.time,
                "sleep",
                side_effect=lambda seconds: sleep_calls.append(float(seconds)),
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(output_root),
                        "--max-loops",
                        "25",
                        "--max-runs",
                        "2",
                        "--restart-delay-seconds",
                        "0",
                        "--failure-backoff-seconds",
                        "3",
                        "--failure-backoff-multiplier",
                        "2",
                    ]
                )

            supervisor_state = json.loads(
                (output_root / "supervisor-state.json").read_text(encoding="utf-8")
            )

        self.assertEqual(len(observed_batch_argv), 3)
        self.assertEqual(sleep_calls, [3.0, 0.0])
        self.assertEqual(result["successful_runs"], 2)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["last_batch_dir"], "2026-07-03/141319")
        self.assertEqual(supervisor_state["state"], "stopped")
        self.assertEqual(supervisor_state["successful_runs"], 2)
        self.assertEqual(supervisor_state["failure_count"], 1)
        self.assertEqual(supervisor_state["last_batch_dir"], "2026-07-03/141319")
        self.assertIn("temporary batch failure", supervisor_state["last_error"])

    def test_cli_passes_batch_flags_through_to_batch_runner(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir)
            observed_batch_argv: list[list[str]] = []

            def fake_batch_cli(argv):
                observed_batch_argv.append(list(argv))
                return {"batch_dir": "2026-07-03/131319"}

            with patch.object(module, "_run_batch_once", side_effect=fake_batch_cli), patch.object(
                module.time,
                "sleep",
                return_value=None,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(output_root),
                        "--max-loops",
                        "123",
                        "--skip-live-snapshot",
                        "--skip-sim-preflight",
                        "--max-runs",
                        "1",
                    ]
                )

        self.assertEqual(result["successful_runs"], 1)
        self.assertEqual(
            observed_batch_argv,
            [[
                "--env-file",
                "server.env",
                "--live-log-path",
                "tmp/server/live_runtime.jsonl",
                "--output-root",
                str(output_root),
                "--max-loops",
                "123",
                "--skip-live-snapshot",
                "--skip-sim-preflight",
            ]],
        )

    def test_cli_reuses_incomplete_runner_batch_dir_on_restart(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir)
            resumed_batch = output_root / "2026-07-04" / "152046" / "simulation"
            resumed_batch.mkdir(parents=True, exist_ok=True)
            (resumed_batch / "live_sim_runtime.jsonl").write_text('{"event":"tick"}\n', encoding="utf-8")
            (output_root / "runner-state.json").write_text(
                json.dumps(
                    {
                        "state": "running",
                        "pid": 12345,
                        "updated_at": "2026-07-04T15:20:44",
                        "batch_dir": "2026-07-04/152046",
                    }
                ),
                encoding="utf-8",
            )
            observed_batch_argv: list[list[str]] = []

            def fake_batch_cli(argv):
                observed_batch_argv.append(list(argv))
                return {"batch_dir": "2026-07-04/152046"}

            with patch.object(module, "_run_batch_once", side_effect=fake_batch_cli), patch.object(
                module.time,
                "sleep",
                return_value=None,
            ):
                result = module.cli(
                    [
                        "--env-file",
                        "server.env",
                        "--live-log-path",
                        "tmp/server/live_runtime.jsonl",
                        "--output-root",
                        str(output_root),
                        "--max-runs",
                        "1",
                    ]
                )

        self.assertEqual(result["last_batch_dir"], "2026-07-04/152046")
        self.assertEqual(
            observed_batch_argv,
            [[
                "--env-file",
                "server.env",
                "--live-log-path",
                "tmp/server/live_runtime.jsonl",
                "--output-root",
                str(output_root),
                "--batch-dir",
                "2026-07-04/152046",
            ]],
        )

    def test_discover_resumable_batch_ignores_non_utf8_runner_state(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir)
            (output_root / "runner-state.json").write_bytes(b"\x82\x00\xff")

            result = module._discover_resumable_batch_dir(output_root)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
