from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from src.app.bootstrap import build_live_preflight
from src.domain.models import PortfolioSnapshot
from src.preflight.checker import PreflightChecker
from src.strategy.position_sizing import OrderSizingRule


class FakeAccountService:
    def __init__(self) -> None:
        self.pm_snapshot = PortfolioSnapshot(
            account_equity=Decimal("10000"),
            available_balance=Decimal("5000"),
            uni_mmr=Decimal("10"),
        )
        self.position_mode = {"dualSidePosition": True}
        self.rules: dict[str, OrderSizingRule | None] = {
            "BTCUSDT": OrderSizingRule(
                step_size=Decimal("0.001"),
                min_qty=Decimal("0.001"),
                min_notional=Decimal("5"),
            ),
            "ETHUSDT": OrderSizingRule(
                step_size=Decimal("0.001"),
                min_qty=Decimal("0.001"),
                min_notional=Decimal("5"),
            ),
            "SOLUSDT": OrderSizingRule(
                step_size=Decimal("0.1"),
                min_qty=Decimal("0.1"),
                min_notional=Decimal("5"),
            ),
        }
        self.calls: list[tuple[str, object]] = []

    def get_pm_account_snapshot(self) -> PortfolioSnapshot:
        self.calls.append(("get_pm_account_snapshot", None))
        return self.pm_snapshot

    def get_um_position_mode(self) -> dict:
        self.calls.append(("get_um_position_mode", None))
        return self.position_mode

    def get_symbol_order_sizing_rule(self, symbol: str) -> OrderSizingRule:
        self.calls.append(("get_symbol_order_sizing_rule", symbol))
        rule = self.rules.get(symbol)
        if rule is None:
            raise ValueError(f"missing rule for {symbol}")
        return rule

    def transfer_pm_to_spot(self, asset: str, amount: str) -> dict:
        self.calls.append(("transfer_pm_to_spot", {"asset": asset, "amount": amount}))
        return {"asset": asset, "amount": amount, "ok": True}

    def subscribe_rwusd(self, amount: str) -> dict:
        self.calls.append(("subscribe_rwusd", {"amount": amount}))
        return {"amount": amount, "ok": True}

    def redeem_rwusd(self, amount: str) -> dict:
        self.calls.append(("redeem_rwusd", {"amount": amount}))
        return {"amount": amount, "ok": True}

    def transfer_spot_to_pm(self, asset: str, amount: str) -> dict:
        self.calls.append(("transfer_spot_to_pm", {"asset": asset, "amount": amount}))
        return {"asset": asset, "amount": amount, "ok": True}


class FakeStreamClient:
    def __init__(self) -> None:
        self.listen_key = "listen-key-1"
        self.fail_start = False
        self.calls: list[str] = []

    def start_user_stream(self) -> dict:
        self.calls.append("start_user_stream")
        if self.fail_start:
            return {}
        return {"listenKey": self.listen_key}

    def keepalive_user_stream(self) -> dict:
        self.calls.append("keepalive_user_stream")
        return {"ok": True}

    def close_user_stream(self) -> dict:
        self.calls.append("close_user_stream")
        return {"ok": True}


class PreflightCheckerTests(unittest.TestCase):
    def test_run_returns_ok_when_all_checks_pass(self) -> None:
        checker = PreflightChecker(
            account_service=FakeAccountService(),
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )

        report = checker.run()

        self.assertTrue(report.ok)
        self.assertEqual(
            [item.name for item in report.checks],
            [
                "pm_account",
                "um_position_mode",
                "symbol_rules",
                "user_stream",
                "rwusd_capability",
            ],
        )
        self.assertTrue(all(item.ok for item in report.checks))

    def test_run_fails_when_um_position_mode_is_not_hedge(self) -> None:
        account_service = FakeAccountService()
        account_service.position_mode = {"dualSidePosition": False}
        checker = PreflightChecker(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )

        report = checker.run()

        self.assertFalse(report.ok)
        self.assertEqual(report.checks[1].name, "um_position_mode")
        self.assertFalse(report.checks[1].ok)
        self.assertEqual(report.checks[1].reason, "hedge_mode_required")

    def test_run_fails_when_symbol_rule_lookup_is_missing(self) -> None:
        account_service = FakeAccountService()
        account_service.rules["SOLUSDT"] = None
        checker = PreflightChecker(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )

        report = checker.run()

        self.assertFalse(report.ok)
        self.assertEqual(report.checks[2].name, "symbol_rules")
        self.assertFalse(report.checks[2].ok)
        self.assertIn("SOLUSDT", report.checks[2].reason)

    def test_run_fails_when_user_stream_cannot_start(self) -> None:
        stream_client = FakeStreamClient()
        stream_client.fail_start = True
        checker = PreflightChecker(
            account_service=FakeAccountService(),
            stream_client=stream_client,
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )

        report = checker.run()

        self.assertFalse(report.ok)
        self.assertEqual(report.checks[3].name, "user_stream")
        self.assertFalse(report.checks[3].ok)
        self.assertEqual(report.checks[3].reason, "listen_key_missing")

    def test_run_fails_when_reverse_rwusd_capability_is_missing(self) -> None:
        account_service = FakeAccountService()
        account_service.redeem_rwusd = None
        checker = PreflightChecker(
            account_service=account_service,
            stream_client=FakeStreamClient(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )

        report = checker.run()

        self.assertFalse(report.ok)
        self.assertEqual(report.checks[4].name, "rwusd_capability")
        self.assertFalse(report.checks[4].ok)
        self.assertIn("redeem_rwusd", report.checks[4].reason)


class BootstrapPreflightTests(unittest.TestCase):
    def test_build_live_preflight_returns_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=test-key",
                        "BINANCE_API_SECRET=test-secret",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                    ]
                ),
                encoding="utf-8",
            )

            preflight = build_live_preflight(config_path=env_file)

            self.assertTrue(callable(preflight))


if __name__ == "__main__":
    unittest.main()
