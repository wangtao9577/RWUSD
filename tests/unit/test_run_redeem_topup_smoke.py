import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_redeem_topup_smoke.py"
    spec = importlib.util.spec_from_file_location("run_redeem_topup_smoke_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunRedeemTopupSmokeScriptTests(unittest.TestCase):
    def test_script_runs_directly_from_cli(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_redeem_topup_smoke.py"

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "cli-smoke"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=str(Path(__file__).resolve().parents[2]),
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_cli_writes_redeem_topup_evidence_files(self) -> None:
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "smoke"
            result = module.cli(
                [
                    "--output-dir",
                    str(output_dir),
                    "--symbol",
                    "BTCUSDT",
                ]
            )

            log_path = output_dir / "live_sim_runtime.jsonl"
            summary_path = output_dir / "runtime-summary.json"
            result_path = output_dir / "smoke-result.json"

            self.assertTrue(log_path.exists())
            self.assertTrue(summary_path.exists())
            self.assertTrue(result_path.exists())

            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(result["output_dir"], str(output_dir))
        self.assertEqual(summary_payload["redeem_topup_count"], 1)
        self.assertEqual(summary_payload["risk_reason_counts"]["available_balance_reserve"], 1)
        self.assertEqual(
            result_payload["account_calls"][:2],
            [
                {"method": "redeem_rwusd", "payload": {"amount": "40"}},
                {"method": "transfer_spot_to_pm", "payload": {"asset": "USDT", "amount": "40"}},
            ],
        )
        self.assertEqual(result_payload["bucket_before"]["rwusd_principal"], "120")
        self.assertEqual(result_payload["bucket_after"]["rwusd_principal"], "80")
        self.assertEqual(result_payload["bucket_after"]["rwusd_redeemable"], "80")
        self.assertEqual(result_payload["bucket_after"]["redeem_count"], 1)
        self.assertEqual(result_payload["event_counts"]["live.redeem_topup_plan"], 1)
        self.assertEqual(result_payload["event_counts"]["live.redeem_topup_executed"], 1)


if __name__ == "__main__":
    unittest.main()
