import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.infra.simulation_batch_report import build_batch_performance_report


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


class SimulationBatchReportTests(unittest.TestCase):
    def test_build_batch_performance_report_computes_current_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            batch_dir = root / "2026-07-02" / "235202"
            runtime_log = batch_dir / "simulation" / "live_sim_runtime.jsonl"
            runtime_log.parent.mkdir(parents=True, exist_ok=True)
            runtime_log.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event": "runtime.loop_completed",
                                "context": {
                                    "loop_count": 1,
                                    "rwusd_principal": "10000",
                                    "rwusd_interest_accrued": "0",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event": "runtime.loop_completed",
                                "context": {
                                    "loop_count": 2,
                                    "rwusd_principal": "10200",
                                    "rwusd_interest_accrued": "0",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event": "runtime.loop_completed",
                                "context": {
                                    "loop_count": 3,
                                    "rwusd_principal": "10100",
                                    "rwusd_interest_accrued": "0",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event": "runtime.loop_completed",
                                "context": {
                                    "loop_count": 300,
                                    "rwusd_principal": "10058.75920820789032137927743",
                                    "rwusd_interest_accrued": "0.05707518163962948200136450941",
                                },
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            _write_json(
                batch_dir / "simulation" / "runtime-summary.json",
                {
                    "loop_completed_count": 300,
                    "selected_symbol_counts": {"SOLUSDT": 300},
                    "profit_sweep_count": 1,
                    "redeem_topup_count": 0,
                    "rwusd_principal": "10058.75920820789032137927743",
                    "rwusd_interest_accrued": "0.05707518163962948200136450941",
                },
            )
            _write_json(
                batch_dir / "simulation" / "simulation-outcome.json",
                {
                    "initial_capital_usdt": "10000",
                    "rwusd_principal": "10058.75920820789032137927743",
                    "rwusd_interest_accrued": "0.05707518163962948200136450941",
                    "profit_sweep_count": 1,
                    "redeem_topup_count": 0,
                    "selected_symbol_counts": {"SOLUSDT": 300},
                    "verdict": "pass",
                },
            )
            _write_json(
                batch_dir / "simulation" / "account-market-snapshot.json",
                {
                    "market": [
                        {"symbol": "BTCUSDT"},
                        {"symbol": "ETHUSDT"},
                        {"symbol": "SOLUSDT"},
                    ],
                    "selected_symbols": ["SOLUSDT"],
                    "strategy": {
                        "phase": "HEDGED",
                        "leverage": "20",
                        "take_profit_count": 2,
                        "restore_count": 2,
                        "rwusd_principal": "10058.75920820789032137927743",
                        "rwusd_interest_accrued": "0.05707518163962948200136450941",
                        "deposit_count": 1,
                        "redeem_count": 0,
                    },
                },
            )

            report = build_batch_performance_report(output_root=root, batch_dir=batch_dir)

        self.assertEqual(report["current_batch"]["batch_dir"], "2026-07-02/235202")
        self.assertEqual(report["current_batch"]["selected_symbol"], "SOLUSDT")
        self.assertAlmostEqual(report["current_batch"]["total_return_pct"], 0.5882, places=4)
        self.assertAlmostEqual(report["current_batch"]["runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["current_batch"]["monthly_return_pct_5m_linear"], 16.94, places=2)
        self.assertAlmostEqual(report["current_batch"]["annualized_return_pct_5m_linear"], 206.09, places=2)
        self.assertAlmostEqual(report["current_batch"]["max_drawdown_pct"], 1.3842, places=4)
        self.assertEqual(len(report["current_batch"]["equity_curve"]), 4)
        self.assertEqual(report["current_batch"]["equity_curve"][0]["loop_count"], 1)
        self.assertAlmostEqual(report["current_batch"]["equity_curve"][1]["total_return_pct"], 2.0, places=4)
        self.assertAlmostEqual(report["current_batch"]["equity_curve"][2]["drawdown_pct"], 0.9804, places=4)
        self.assertEqual(report["pdf_alignment"]["completion_pct"], 100)
        self.assertEqual(report["pdf_alignment"]["completed_count"], 11)
        self.assertEqual(report["pdf_alignment"]["partial_count"], 0)
        self.assertEqual(report["pdf_alignment"]["pending_count"], 0)
        self.assertEqual(report["observation_archive"]["batch_dir"], "2026-07-02/235202")
        self.assertEqual(report["observation_archive"]["selected_symbol"], "SOLUSDT")
        self.assertAlmostEqual(report["observation_archive"]["principal_growth_usdt"], 58.7592, places=4)
        self.assertAlmostEqual(report["observation_archive"]["interest_growth_usdt"], 0.0571, places=4)
        self.assertAlmostEqual(report["observation_archive"]["current_total_return_pct"], 0.5882, places=4)
        self.assertAlmostEqual(report["observation_archive"]["current_runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["observation_archive"]["best_runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["observation_archive"]["gap_to_best_return_pct"], 0.0, places=4)
        self.assertEqual(len(report["observation_archive"]["current_equity_curve"]), 4)
        self.assertEqual(report["observation_archive"]["verdict"], "pass")
        self.assertTrue(report["observation_archive"]["updated_at"])
        self.assertEqual(report["experiment_summary"]["batch_count"], 1)
        self.assertEqual(report["experiment_summary"]["pass_batch_count"], 1)
        self.assertAlmostEqual(report["experiment_summary"]["pass_rate_pct"], 100.0, places=2)
        self.assertEqual(report["experiment_summary"]["stable_batch_count"], 1)
        self.assertEqual(report["experiment_summary"]["stable_pass_batch_count"], 1)
        self.assertEqual(report["experiment_summary"]["current_batch_dir"], "2026-07-02/235202")
        self.assertEqual(report["experiment_summary"]["best_batch_dir"], "2026-07-02/235202")
        self.assertAlmostEqual(report["experiment_summary"]["current_runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["experiment_summary"]["best_runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["experiment_summary"]["average_runtime_days"], 1.0417, places=4)
        self.assertEqual(report["experiment_summary"]["selected_symbol_counts"]["SOLUSDT"], 1)
        self.assertAlmostEqual(report["experiment_summary"]["average_total_return_pct"], 0.5882, places=4)
        self.assertTrue(report["experiment_summary"]["updated_at"])

    def test_build_batch_performance_report_picks_best_pass_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current_dir = root / "2026-07-02" / "235202"
            best_dir = root / "2026-07-02" / "002848"
            for batch_dir, principal, interest, loops in (
                (current_dir, "10058.75920820789032137927743", "0.05707518163962948200136450941", 300),
                (best_dir, "10174.71307206398755810321518", "0.1153657881476319889523266341", 600),
            ):
                _write_json(
                    batch_dir / "simulation" / "runtime-summary.json",
                    {
                        "loop_completed_count": loops,
                        "selected_symbol_counts": {"SOLUSDT": loops},
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "rwusd_principal": principal,
                        "rwusd_interest_accrued": interest,
                    },
                )
                _write_json(
                    batch_dir / "simulation" / "simulation-outcome.json",
                    {
                        "initial_capital_usdt": "10000",
                        "rwusd_principal": principal,
                        "rwusd_interest_accrued": interest,
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "selected_symbol_counts": {"SOLUSDT": loops},
                        "verdict": "pass",
                    },
                )
                _write_json(
                    batch_dir / "simulation" / "account-market-snapshot.json",
                    {
                        "market": [
                            {"symbol": "BTCUSDT"},
                            {"symbol": "ETHUSDT"},
                            {"symbol": "SOLUSDT"},
                        ],
                        "selected_symbols": ["SOLUSDT"],
                        "strategy": {
                            "phase": "HEDGED",
                            "leverage": "20",
                            "take_profit_count": 2,
                            "restore_count": 2,
                            "rwusd_principal": principal,
                            "rwusd_interest_accrued": interest,
                            "deposit_count": 1,
                            "redeem_count": 0,
                        },
                    },
                )

            report = build_batch_performance_report(output_root=root, batch_dir=current_dir)

        self.assertEqual(report["best_batch"]["batch_dir"], "2026-07-02/002848")
        self.assertAlmostEqual(report["best_batch"]["total_return_pct"], 1.7483, places=4)
        self.assertAlmostEqual(report["comparison"]["gap_to_best_return_pct"], -1.1601, places=4)
        self.assertEqual(report["experiment_summary"]["batch_count"], 2)
        self.assertEqual(report["experiment_summary"]["pass_batch_count"], 2)
        self.assertEqual(report["experiment_summary"]["selected_symbol_counts"]["SOLUSDT"], 2)
        self.assertAlmostEqual(report["experiment_summary"]["average_total_return_pct"], 1.1683, places=4)
        self.assertAlmostEqual(report["experiment_summary"]["current_runtime_days"], 1.0417, places=4)
        self.assertAlmostEqual(report["experiment_summary"]["best_runtime_days"], 2.0833, places=4)
        self.assertAlmostEqual(report["experiment_summary"]["average_runtime_days"], 1.5625, places=4)
        self.assertEqual(report["experiment_summary"]["best_batch_dir"], "2026-07-02/002848")

    def test_build_batch_performance_report_prefers_stable_best_batch_over_short_burst(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current_dir = root / "2026-07-03" / "004233"
            short_burst_dir = root / "2026-07-01" / "231516"
            stable_best_dir = root / "2026-07-02" / "002848"
            fixtures = (
                (current_dir, "10000", "0.0036", 20),
                (short_burst_dir, "10515.63174937133552869406301", "0.01899840575793642391997676546", 100),
                (stable_best_dir, "10174.71307206398755810321518", "0.1153657881476319889523266341", 600),
            )
            for batch_dir, principal, interest, loops in fixtures:
                _write_json(
                    batch_dir / "simulation" / "runtime-summary.json",
                    {
                        "loop_completed_count": loops,
                        "selected_symbol_counts": {"SOLUSDT": loops},
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "rwusd_principal": principal,
                        "rwusd_interest_accrued": interest,
                    },
                )
                _write_json(
                    batch_dir / "simulation" / "simulation-outcome.json",
                    {
                        "initial_capital_usdt": "10000",
                        "rwusd_principal": principal,
                        "rwusd_interest_accrued": interest,
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "selected_symbol_counts": {"SOLUSDT": loops},
                        "verdict": "pass",
                    },
                )
                _write_json(
                    batch_dir / "simulation" / "account-market-snapshot.json",
                    {
                        "market": [
                            {"symbol": "BTCUSDT"},
                            {"symbol": "ETHUSDT"},
                            {"symbol": "SOLUSDT"},
                        ],
                        "selected_symbols": ["SOLUSDT"],
                        "strategy": {
                            "phase": "HEDGED",
                            "leverage": "20",
                            "take_profit_count": 2,
                            "restore_count": 2,
                            "rwusd_principal": principal,
                            "rwusd_interest_accrued": interest,
                            "deposit_count": 1,
                            "redeem_count": 0,
                        },
                    },
                )

            report = build_batch_performance_report(output_root=root, batch_dir=current_dir)

        self.assertEqual(report["best_batch"]["batch_dir"], "2026-07-02/002848")
