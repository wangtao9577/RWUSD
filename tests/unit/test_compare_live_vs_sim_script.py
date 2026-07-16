import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "compare_live_vs_sim.py"
    spec = importlib.util.spec_from_file_location("compare_live_vs_sim_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompareLiveVsSimScriptTests(unittest.TestCase):
    def test_cli_dispatches_to_comparison_writer(self) -> None:
        module = _load_script_module()
        fake_result = {"matches": {"selected_symbol_counts": True}, "mismatches": []}

        with patch.object(
            module,
            "write_live_sim_comparison_report",
            return_value=fake_result,
        ) as mocked_writer:
            result = module.cli(
                [
                    "--live-summary-path",
                    "live-summary.json",
                    "--sim-summary-path",
                    "sim-summary.json",
                    "--live-snapshot-path",
                    "live-snapshot.json",
                    "--sim-snapshot-path",
                    "sim-snapshot.json",
                    "--output-path",
                    "comparison.json",
                ]
            )

        self.assertEqual(result, fake_result)
        mocked_writer.assert_called_once_with(
            live_summary_path="live-summary.json",
            sim_summary_path="sim-summary.json",
            output_path="comparison.json",
            live_snapshot_path="live-snapshot.json",
            sim_snapshot_path="sim-snapshot.json",
        )


if __name__ == "__main__":
    unittest.main()
