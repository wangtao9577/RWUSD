import json
import tempfile
import unittest
from pathlib import Path

from src.infra.live_sim_comparison import (
    build_live_sim_comparison_report,
    write_live_sim_comparison_report,
)


class LiveSimComparisonTests(unittest.TestCase):
    def test_build_report_compares_summary_and_snapshot_differences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            live_summary_path = base / "live-runtime-summary.json"
            sim_summary_path = base / "sim-runtime-summary.json"
            live_snapshot_path = base / "live-account-market-snapshot.json"
            sim_snapshot_path = base / "sim-account-market-snapshot.json"

            live_summary_path.write_text(
                json.dumps(
                    {
                        "selected_symbol_counts": {"BTCUSDT": 2, "ETHUSDT": 1},
                        "rebalance_action_counts": {"restore_now": 1},
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            sim_summary_path.write_text(
                json.dumps(
                    {
                        "selected_symbol_counts": {"BTCUSDT": 1, "SOLUSDT": 1},
                        "rebalance_action_counts": {"restore_later": 1},
                        "profit_sweep_count": 0,
                        "redeem_topup_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            live_snapshot_path.write_text(
                json.dumps(
                    {
                        "account": {
                            "account_equity": "1000",
                            "available_balance": "300",
                            "uni_mmr": "7.5",
                        },
                        "positions": [{"symbol": "BTCUSDT"}],
                        "market": [
                            {"symbol": "BTCUSDT", "close_price": "60000", "funding_rate": "0.0001"},
                            {"symbol": "ETHUSDT", "close_price": "3000", "funding_rate": "0.0002"},
                        ],
                        "selected_symbols": ["BTCUSDT"],
                    }
                ),
                encoding="utf-8",
            )
            sim_snapshot_path.write_text(
                json.dumps(
                    {
                        "account": {
                            "account_equity": "980",
                            "available_balance": "260",
                            "uni_mmr": "7.1",
                        },
                        "positions": [{"symbol": "SOLUSDT"}],
                        "market": [
                            {"symbol": "BTCUSDT", "close_price": "59950", "funding_rate": "0.0001"},
                            {"symbol": "SOLUSDT", "close_price": "150", "funding_rate": "0.0003"},
                        ],
                        "selected_symbols": ["SOLUSDT"],
                    }
                ),
                encoding="utf-8",
            )

            report = build_live_sim_comparison_report(
                live_summary_path=live_summary_path,
                sim_summary_path=sim_summary_path,
                live_snapshot_path=live_snapshot_path,
                sim_snapshot_path=sim_snapshot_path,
            )

            self.assertEqual(report["summary"]["selected_symbol_count_diffs"]["BTCUSDT"], -1)
            self.assertEqual(report["summary"]["selected_symbol_count_diffs"]["ETHUSDT"], -1)
            self.assertEqual(report["summary"]["selected_symbol_count_diffs"]["SOLUSDT"], 1)
            self.assertEqual(report["summary"]["rebalance_action_count_diffs"]["restore_now"], -1)
            self.assertEqual(report["summary"]["rebalance_action_count_diffs"]["restore_later"], 1)
            self.assertEqual(report["summary"]["profit_sweep_count_diff"], -1)
            self.assertEqual(report["summary"]["redeem_topup_count_diff"], 1)
            self.assertFalse(report["matches"]["selected_symbol_counts"])
            self.assertFalse(report["matches"]["rebalance_action_counts"])
            self.assertFalse(report["matches"]["snapshot_selected_symbols"])
            self.assertFalse(report["matches"]["snapshot_position_symbols"])
            self.assertEqual(report["snapshot"]["selected_symbols"]["live"], ["BTCUSDT"])
            self.assertEqual(report["snapshot"]["selected_symbols"]["sim"], ["SOLUSDT"])
            self.assertEqual(report["snapshot"]["position_symbols_only_in_live"], ["BTCUSDT"])
            self.assertEqual(report["snapshot"]["position_symbols_only_in_sim"], ["SOLUSDT"])
            self.assertEqual(report["snapshot"]["account_metric_deltas"]["account_equity"], "-20")
            self.assertEqual(report["snapshot"]["account_metric_deltas"]["available_balance"], "-40")
            self.assertEqual(report["snapshot"]["account_metric_deltas"]["uni_mmr"], "-0.4")
            self.assertEqual(
                report["snapshot"]["market_deltas"]["BTCUSDT"]["close_price_delta"],
                "-50",
            )
            self.assertIn("selected_symbol_counts", report["mismatches"])
            self.assertIn("snapshot_selected_symbols", report["mismatches"])

    def test_write_report_persists_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            live_summary_path = base / "live-summary.json"
            sim_summary_path = base / "sim-summary.json"
            output_path = base / "reports" / "live-sim-comparison.json"
            live_summary_path.write_text(
                json.dumps({"selected_symbol_counts": {}, "rebalance_action_counts": {}, "profit_sweep_count": 0, "redeem_topup_count": 0}),
                encoding="utf-8",
            )
            sim_summary_path.write_text(
                json.dumps({"selected_symbol_counts": {}, "rebalance_action_counts": {}, "profit_sweep_count": 0, "redeem_topup_count": 0}),
                encoding="utf-8",
            )

            report = write_live_sim_comparison_report(
                live_summary_path=live_summary_path,
                sim_summary_path=sim_summary_path,
                output_path=output_path,
            )

            self.assertTrue(output_path.exists())
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["matches"]["selected_symbol_counts"], True)
            self.assertEqual(report["mismatches"], [])


if __name__ == "__main__":
    unittest.main()
