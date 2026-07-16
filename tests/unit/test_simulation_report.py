import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.domain.models import PortfolioSnapshot
from src.exchange.binance_account import UmHedgePosition
from src.infra.simulation_outcome import (
    build_simulation_outcome,
    write_simulation_outcome,
)
from src.infra.simulation_report import build_runtime_summary, write_runtime_summary
from src.app.simulation_snapshot import (
    build_account_market_snapshot,
    collect_account_market_snapshot,
    write_account_market_snapshot,
)


class SimulationReportTests(unittest.TestCase):
    def test_build_simulation_outcome_marks_pass_when_rwusd_grows_without_hard_risk(self) -> None:
        summary = {
            "selected_symbol_counts": {"ETHUSDT": 3},
            "risk_reason_counts": {"uni_mmr_soft_limit": 1},
            "rebalance_action_counts": {"restore_now": 2, "hold": 1},
            "profit_sweep_count": 2,
            "redeem_topup_count": 0,
            "rwusd_principal": "180",
            "rwusd_interest_accrued": "4.2",
        }
        snapshot = {
            "account": {
                "uni_mmr": "13.4",
            },
            "strategy": {
                "harvest_buffer": "26",
            },
        }

        outcome = build_simulation_outcome(summary=summary, snapshot=snapshot)

        self.assertEqual(outcome["initial_capital_usdt"], "10000")
        self.assertEqual(outcome["rwusd_principal"], "180")
        self.assertEqual(outcome["rwusd_interest_accrued"], "4.2")
        self.assertEqual(outcome["harvest_buffer"], "26")
        self.assertEqual(outcome["profit_sweep_count"], 2)
        self.assertEqual(outcome["redeem_topup_count"], 0)
        self.assertEqual(
            outcome["rebalance_action_counts"],
            {"restore_now": 2, "hold": 1},
        )
        self.assertEqual(outcome["selected_symbol_counts"], {"ETHUSDT": 3})
        self.assertEqual(
            outcome["uni_mmr"],
            {
                "current": "13.4",
                "soft_limit_breach_count": 1,
                "hard_limit_breach_count": 0,
                "health": "healthy",
            },
        )
        self.assertEqual(outcome["verdict"], "pass")

    def test_build_simulation_outcome_marks_borderline_when_redeem_topup_occurs(self) -> None:
        outcome = build_simulation_outcome(
            summary={
                "selected_symbol_counts": {"BTCUSDT": 2},
                "risk_reason_counts": {"uni_mmr_soft_limit": 2},
                "rebalance_action_counts": {"reduce_risk": 1},
                "profit_sweep_count": 1,
                "redeem_topup_count": 1,
                "rwusd_principal": "95",
                "rwusd_interest_accrued": "1.5",
            },
            snapshot={
                "account": {"uni_mmr": "9.8"},
                "strategy": {"harvest_buffer": "8"},
            },
        )

        self.assertEqual(outcome["verdict"], "borderline")
        self.assertEqual(outcome["uni_mmr"]["health"], "soft_limit_seen")

    def test_build_simulation_outcome_marks_fail_when_hard_risk_occurs(self) -> None:
        outcome = build_simulation_outcome(
            summary={
                "selected_symbol_counts": {"SOLUSDT": 1},
                "risk_reason_counts": {"uni_mmr_hard_limit": 1},
                "rebalance_action_counts": {"reduce_risk": 1},
                "profit_sweep_count": 0,
                "redeem_topup_count": 1,
                "rwusd_principal": "0",
                "rwusd_interest_accrued": "0",
            },
            snapshot={
                "account": {"uni_mmr": "7.2"},
                "strategy": {"harvest_buffer": "0"},
            },
        )

        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["uni_mmr"]["health"], "hard_limit_seen")

    def test_write_simulation_outcome_creates_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "reports" / "simulation-outcome.json"
            result = write_simulation_outcome(
                summary={
                    "selected_symbol_counts": {"BTCUSDT": 1},
                    "risk_reason_counts": {},
                    "rebalance_action_counts": {"hold": 1},
                    "profit_sweep_count": 1,
                    "redeem_topup_count": 0,
                    "rwusd_principal": "40",
                    "rwusd_interest_accrued": "0.5",
                },
                snapshot={
                    "account": {"uni_mmr": "12.1"},
                    "strategy": {"harvest_buffer": "4"},
                },
                output_path=output_path,
            )

            self.assertTrue(output_path.exists())
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["rwusd_principal"], "40")
            self.assertEqual(result["verdict"], "pass")

    def test_build_runtime_summary_counts_events_and_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "live_runtime-2026-06-28.jsonl"
            records = [
                {
                    "level": "INFO",
                    "message": "runtime loop completed",
                    "event": "runtime.loop_completed",
                    "context": {
                        "loop_count": 1,
                        "selected_symbols": ["BTCUSDT", "ETHUSDT"],
                        "risk_reasons": ["uni_mmr_soft_limit", None],
                        "intent_actions": ["open_hedge", "hold"],
                        "rebalance_actions": ["restore_later", None],
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "rwusd_principal": "50",
                        "rwusd_interest_accrued": "2.5",
                        "harvest_buffer": "12",
                        "closed_loop_ready": False,
                        "last_rebalance_action": "restore_later",
                        "sweep_block_reason": "pending_rebalance",
                    },
                },
                {
                    "level": "WARN",
                    "message": "runtime cycle input provider failed",
                    "event": "runtime.loop_retry",
                    "context": {
                        "error": "temporary",
                        "attempts_remaining": 1,
                        "backoff_seconds": 1.0,
                    },
                },
                {
                    "level": "INFO",
                    "message": "runtime loop completed",
                    "event": "runtime.loop_completed",
                    "context": {
                        "loop_count": 2,
                        "selected_symbols": ["BTCUSDT"],
                        "risk_reasons": ["uni_mmr_hard_limit"],
                        "intent_actions": ["reduce_risk"],
                        "rebalance_actions": ["reduce_risk"],
                        "profit_sweep_count": 0,
                        "redeem_topup_count": 1,
                        "rwusd_principal": "70",
                        "rwusd_interest_accrued": "3.0",
                        "harvest_buffer": "4",
                        "closed_loop_ready": False,
                        "last_rebalance_action": "reduce_risk",
                        "sweep_block_reason": "risk_block",
                    },
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            summary = build_runtime_summary(log_path)

            self.assertEqual(summary["loop_completed_count"], 2)
            self.assertEqual(summary["loop_retry_count"], 1)
            self.assertEqual(
                summary["selected_symbol_counts"],
                {
                    "BTCUSDT": 2,
                    "ETHUSDT": 1,
                },
            )
            self.assertEqual(
                summary["risk_reason_counts"],
                {
                    "uni_mmr_soft_limit": 1,
                    "uni_mmr_hard_limit": 1,
                },
            )
            self.assertEqual(
                summary["intent_action_counts"],
                {
                    "open_hedge": 1,
                    "hold": 1,
                    "reduce_risk": 1,
                },
            )
            self.assertEqual(
                summary["rebalance_action_counts"],
                {
                    "restore_later": 1,
                    "reduce_risk": 1,
                },
            )
            self.assertEqual(summary["profit_sweep_count"], 1)
            self.assertEqual(summary["redeem_topup_count"], 1)
            self.assertEqual(summary["rwusd_principal"], "70")
            self.assertEqual(summary["rwusd_interest_accrued"], "3.0")
            self.assertEqual(summary["harvest_buffer"], "4")
            self.assertFalse(summary["closed_loop_ready"])
            self.assertEqual(summary["last_rebalance_action"], "reduce_risk")
            self.assertEqual(summary["sweep_block_reason"], "risk_block")

    def test_build_runtime_summary_does_not_double_count_live_detail_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "live_runtime-2026-06-30.jsonl"
            records = [
                {
                    "level": "INFO",
                    "message": "rebalance decision",
                    "event": "live.rebalance_decision",
                    "context": {
                        "action": "restore_later",
                    },
                },
                {
                    "level": "INFO",
                    "message": "profit sweep executed",
                    "event": "live.profit_sweep_executed",
                    "context": {},
                },
                {
                    "level": "INFO",
                    "message": "redeem topup executed",
                    "event": "live.redeem_topup_executed",
                    "context": {},
                },
                {
                    "level": "INFO",
                    "message": "runtime loop completed",
                    "event": "runtime.loop_completed",
                    "context": {
                        "loop_count": 1,
                        "selected_symbols": ["BTCUSDT"],
                        "risk_reasons": [],
                        "intent_actions": ["hold"],
                        "rebalance_actions": ["restore_later"],
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 1,
                        "rwusd_principal": "80",
                        "rwusd_interest_accrued": "3.5",
                    },
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            summary = build_runtime_summary(log_path)

            self.assertEqual(
                summary["rebalance_action_counts"],
                {
                    "restore_later": 1,
                },
            )
            self.assertEqual(summary["profit_sweep_count"], 1)
            self.assertEqual(summary["redeem_topup_count"], 1)

    def test_build_runtime_summary_counts_mixed_loop_summary_and_detail_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "live_runtime-2026-06-30-mixed.jsonl"
            records = [
                {
                    "level": "INFO",
                    "message": "rebalance decision",
                    "event": "live.rebalance_decision",
                    "context": {
                        "action": "restore_later",
                    },
                },
                {
                    "level": "INFO",
                    "message": "profit sweep executed",
                    "event": "live.profit_sweep_executed",
                    "context": {},
                },
                {
                    "level": "INFO",
                    "message": "runtime loop completed",
                    "event": "runtime.loop_completed",
                    "context": {
                        "loop_count": 1,
                        "selected_symbols": ["BTCUSDT"],
                        "risk_reasons": [],
                        "intent_actions": ["hold"],
                        "rebalance_actions": ["restore_later"],
                        "profit_sweep_count": 1,
                        "redeem_topup_count": 0,
                        "rwusd_principal": "80",
                        "rwusd_interest_accrued": "3.5",
                    },
                },
                {
                    "level": "INFO",
                    "message": "rebalance decision",
                    "event": "live.rebalance_decision",
                    "context": {
                        "action": "reduce_risk",
                    },
                },
                {
                    "level": "INFO",
                    "message": "redeem topup executed",
                    "event": "live.redeem_topup_executed",
                    "context": {},
                },
                {
                    "level": "INFO",
                    "message": "runtime loop completed",
                    "event": "runtime.loop_completed",
                    "context": {
                        "loop_count": 2,
                        "selected_symbols": ["ETHUSDT"],
                        "risk_reasons": [],
                        "intent_actions": ["reduce_risk"],
                        "rwusd_principal": "85",
                        "rwusd_interest_accrued": "3.8",
                    },
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            summary = build_runtime_summary(log_path)

            self.assertEqual(
                summary["rebalance_action_counts"],
                {
                    "restore_later": 1,
                    "reduce_risk": 1,
                },
            )
            self.assertEqual(summary["profit_sweep_count"], 1)
            self.assertEqual(summary["redeem_topup_count"], 1)

    def test_build_account_market_snapshot_returns_expected_shape(self) -> None:
        snapshot = build_account_market_snapshot(
            account_snapshot=PortfolioSnapshot(
                account_equity=Decimal("1234.56"),
                available_balance=Decimal("456.78"),
                uni_mmr=Decimal("7.25"),
            ),
            hedge_positions=[
                UmHedgePosition(
                    symbol="BTCUSDT",
                    long_qty=Decimal("0.010"),
                    short_qty=Decimal("0.010"),
                    long_notional=Decimal("600"),
                    short_notional=Decimal("598"),
                )
            ],
            market_rows=[
                {
                    "symbol": "BTCUSDT",
                    "close_price": "60000",
                    "funding_rate": "0.0001",
                    "liquidity_score": "0.92",
                    "margin_efficiency_score": "0.87",
                },
                {
                    "symbol": "ETHUSDT",
                    "close_price": "3000",
                    "funding_rate": "-0.0002",
                    "liquidity_score": "0.81",
                    "margin_efficiency_score": "0.78",
                },
            ],
            selected_symbols=["BTCUSDT"],
        )

        self.assertEqual(snapshot["account"]["uni_mmr"], "7.25")
        self.assertEqual(snapshot["account"]["available_balance"], "456.78")
        self.assertEqual(snapshot["selected_symbols"], ["BTCUSDT"])
        self.assertEqual(snapshot["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(snapshot["positions"][0]["long_qty"], "0.010")
        self.assertEqual(snapshot["market"][1]["symbol"], "ETHUSDT")
        self.assertEqual(snapshot["market"][1]["funding_rate"], "-0.0002")

    def test_build_account_market_snapshot_exposes_pdf_monitoring_fields(self) -> None:
        snapshot = build_account_market_snapshot(
            account_snapshot=PortfolioSnapshot(
                account_equity=Decimal("1234.56"),
                available_balance=Decimal("456.78"),
                uni_mmr=Decimal("7.25"),
            ),
            hedge_positions=[],
            market_rows=[
                {
                    "symbol": "BTCUSDT",
                    "close_price": "60000",
                    "funding_rate": "0.0001",
                }
            ],
            selected_symbols=["BTCUSDT"],
            strategy_state={
                "phase": "HarvestWatch",
                "leverage": 20,
                "long_entry": "60000",
                "short_entry": "60010",
                "long_unrealized": "12.5",
                "short_unrealized": "-4.5",
                "take_profit_count": 3,
                "restore_count": 2,
                "harvest_buffer": "30",
                "rwusd_principal": "50",
                "rwusd_interest_accrued": "2.5",
                "harvest_count": 3,
                "deposit_count": 2,
                "redeem_count": 1,
            },
        )

        self.assertEqual(snapshot["strategy"]["phase"], "HarvestWatch")
        self.assertEqual(snapshot["strategy"]["leverage"], 20)
        self.assertEqual(snapshot["strategy"]["long_entry"], "60000")
        self.assertEqual(snapshot["strategy"]["short_entry"], "60010")
        self.assertEqual(snapshot["strategy"]["long_unrealized"], "12.5")
        self.assertEqual(snapshot["strategy"]["short_unrealized"], "-4.5")
        self.assertEqual(snapshot["strategy"]["take_profit_count"], 3)
        self.assertEqual(snapshot["strategy"]["restore_count"], 2)
        self.assertEqual(snapshot["strategy"]["rwusd_principal"], "50")
        self.assertEqual(snapshot["strategy"]["rwusd_interest_accrued"], "2.5")
        self.assertEqual(snapshot["strategy"]["harvest_count"], 3)
        self.assertEqual(snapshot["strategy"]["deposit_count"], 2)
        self.assertEqual(snapshot["strategy"]["redeem_count"], 1)

    def test_write_runtime_summary_creates_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "live_runtime-2026-06-28.jsonl"
            output_path = Path(tmp_dir) / "reports" / "runtime-summary.json"
            log_path.write_text(
                json.dumps(
                    {
                        "level": "INFO",
                        "message": "runtime loop completed",
                        "event": "runtime.loop_completed",
                        "context": {
                            "loop_count": 1,
                            "selected_symbols": ["SOLUSDT"],
                            "risk_reasons": [],
                            "intent_actions": ["hold"],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_summary(log_path=log_path, output_path=output_path)

            self.assertTrue(output_path.exists())
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["selected_symbol_counts"], {"SOLUSDT": 1})
            self.assertEqual(summary["intent_action_counts"], {"hold": 1})

    def test_collect_account_market_snapshot_writes_json_output(self) -> None:
        class FakeAccountService:
            def get_pm_account_snapshot(self):
                return PortfolioSnapshot(
                    account_equity=Decimal("2222.22"),
                    available_balance=Decimal("888.88"),
                    uni_mmr=Decimal("9.50"),
                )

            def get_um_hedge_positions(self):
                return [
                    UmHedgePosition(
                        symbol="ETHUSDT",
                        long_qty=Decimal("0.50"),
                        short_qty=Decimal("0.50"),
                        long_notional=Decimal("1500"),
                        short_notional=Decimal("1498"),
                    )
                ]

        class FakeMarketDataService:
            def get_recent_klines(self, symbol: str, interval: str, limit: int):
                return [
                    [0, "3000", "3010", "2990", "3005", "1000"],
                    [1, "3005", "3020", "3000", "3015", "1200"],
                ]

            def get_premium_index(self, symbol: str):
                return {"lastFundingRate": "0.0003"}

        snapshot = collect_account_market_snapshot(
            account_service=FakeAccountService(),
            market_data_service=FakeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT"],
            interval="5m",
            selected_symbols=["ETHUSDT"],
        )

        self.assertEqual(snapshot["account"]["account_equity"], "2222.22")
        self.assertEqual(snapshot["positions"][0]["symbol"], "ETHUSDT")
        self.assertEqual(snapshot["market"][0]["symbol"], "BTCUSDT")
        self.assertEqual(snapshot["market"][0]["close_price"], "3015")
        self.assertEqual(snapshot["market"][1]["funding_rate"], "0.0003")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "reports" / "account-market-snapshot.json"

            persisted_snapshot = write_account_market_snapshot(
                output_path=output_path,
                account_service=FakeAccountService(),
                market_data_service=FakeMarketDataService(),
                candidate_symbols=["BTCUSDT", "ETHUSDT"],
                interval="5m",
                selected_symbols=["ETHUSDT"],
            )

            self.assertTrue(output_path.exists())
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["selected_symbols"], ["ETHUSDT"])
            self.assertEqual(
                persisted_snapshot["market"][0]["close_price"],
                "3015",
            )

    def test_write_account_market_snapshot_can_fallback_when_account_access_fails(self) -> None:
        class FailingAccountService:
            def get_pm_account_snapshot(self):
                raise RuntimeError("HTTP Error 401: Unauthorized")

            def get_um_hedge_positions(self):
                raise RuntimeError("HTTP Error 401: Unauthorized")

        class FakeMarketDataService:
            def get_recent_klines(self, symbol: str, interval: str, limit: int):
                return [
                    [0, "3000", "3010", "2990", "3005", "1000"],
                    [1, "3005", "3020", "3000", "3015", "1200"],
                ]

            def get_premium_index(self, symbol: str):
                return {"lastFundingRate": "0.0003"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "reports" / "account-market-snapshot.json"

            persisted_snapshot = write_account_market_snapshot(
                output_path=output_path,
                account_service=FailingAccountService(),
                market_data_service=FakeMarketDataService(),
                candidate_symbols=["BTCUSDT", "ETHUSDT"],
                interval="5m",
                selected_symbols=["ETHUSDT"],
                strategy_state={"phase": "HarvestWatch"},
                allow_account_fallback=True,
            )

            self.assertTrue(output_path.exists())
            persisted = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["account"]["account_equity"], "0")
            self.assertEqual(persisted["positions"], [])
            self.assertTrue(persisted["strategy"]["account_snapshot_fallback"])
            self.assertEqual(persisted_snapshot["market"][0]["close_price"], "3015")


if __name__ == "__main__":
    unittest.main()
