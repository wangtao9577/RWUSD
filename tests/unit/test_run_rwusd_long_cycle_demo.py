import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_rwusd_long_cycle_demo.py"
    spec = importlib.util.spec_from_file_location("run_rwusd_long_cycle_demo_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunRwusdLongCycleDemoTests(unittest.TestCase):
    def test_cli_writes_markdown_and_json_report(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "demo"
            result = module.cli(["--output-dir", str(output_dir)])

            report_json = output_dir / "report.json"
            report_md = output_dir / "report.md"
            report_json_exists = report_json.exists()
            report_md_exists = report_md.exists()
            persisted = json.loads(report_json.read_text(encoding="utf-8"))
            markdown_text = report_md.read_text(encoding="utf-8")

        self.assertTrue(report_json_exists)
        self.assertTrue(report_md_exists)
        self.assertEqual(result["summary"]["cycle_count"], 12)
        self.assertEqual(result["summary"]["final_symbol"], "BTCUSDT")
        self.assertEqual(result["summary"]["final_phase"], "HEDGED")
        self.assertEqual(result["summary"]["take_profit_total"], 7)
        self.assertEqual(result["summary"]["restore_now_total"], 7)
        self.assertEqual(result["summary"]["restore_later_total"], 3)
        self.assertEqual(result["summary"]["profit_sweep_total"], 7)
        self.assertEqual(result["summary"]["realized_pnl_total"], "572")
        self.assertEqual(result["summary"]["rwusd_principal"], "10572")
        self.assertTrue(float(result["summary"]["monthly_return_pct_linear"]) > 0.0)
        self.assertTrue(float(result["summary"]["annualized_return_pct_linear"]) > 0.0)
        self.assertEqual(persisted["summary"]["cycle_count"], 12)
        self.assertEqual(len(persisted["history"]), 12)
        self.assertIn("RWUSD 长周期演示报告", markdown_text)
