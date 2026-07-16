import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_live_sim_runtime.py"
    spec = importlib.util.spec_from_file_location("run_live_sim_runtime_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunLiveSimRuntimeScriptTests(unittest.TestCase):
    def test_cli_runs_preflight_then_sim_runtime(self) -> None:
        module = _load_script_module()
        call_order: list[tuple[str, object]] = []

        def fake_preflight():
            call_order.append(("preflight", None))
            return {"status": "ok"}

        def fake_runtime(*, max_loops=None):
            call_order.append(("runtime", max_loops))
            return {
                "status": "started",
                "max_loops": max_loops,
                "outcome": {"verdict": "pass"},
            }

        with patch.object(
            module,
            "build_live_preflight",
            return_value=fake_preflight,
        ) as mocked_preflight_builder, patch.object(
            module,
            "build_live_sim_runtime",
            return_value=fake_runtime,
        ) as mocked_runtime_builder:
            result = module.cli(["--env-file", "sample.env", "--max-loops", "7"])

        self.assertEqual(
            result,
            {
                "preflight": {"status": "ok"},
                "runtime": {
                    "status": "started",
                    "max_loops": 7,
                    "outcome": {"verdict": "pass"},
                },
                "outcome": {"verdict": "pass"},
            },
        )
        mocked_preflight_builder.assert_called_once_with(config_path="sample.env")
        mocked_runtime_builder.assert_called_once_with(config_path="sample.env")
        self.assertEqual(call_order, [("preflight", None), ("runtime", 7)])

    def test_cli_can_skip_preflight(self) -> None:
        module = _load_script_module()

        def fake_runtime(*, max_loops=None):
            return {
                "status": "started",
                "max_loops": max_loops,
                "outcome": {"verdict": "borderline"},
            }

        with patch.object(
            module,
            "build_live_preflight",
        ) as mocked_preflight_builder, patch.object(
            module,
            "build_live_sim_runtime",
            return_value=fake_runtime,
        ) as mocked_runtime_builder:
            result = module.cli(["--skip-preflight"])

        self.assertEqual(
            result,
            {
                "preflight": None,
                "runtime": {
                    "status": "started",
                    "max_loops": None,
                    "outcome": {"verdict": "borderline"},
                },
                "outcome": {"verdict": "borderline"},
            },
        )
        mocked_preflight_builder.assert_not_called()
        mocked_runtime_builder.assert_called_once_with(config_path=".env")


if __name__ == "__main__":
    unittest.main()
