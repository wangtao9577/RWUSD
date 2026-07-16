import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.app.bootstrap import build_live_market_cycle_input_provider
from src.app.live_cycle_inputs import LiveMarketCycleInputProvider
from src.domain.models import PortfolioSnapshot


class FakeAccountService:
    def __init__(self) -> None:
        self.snapshots = [
            PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("600"),
                uni_mmr=Decimal("10"),
            ),
            PortfolioSnapshot(
                account_equity=Decimal("900"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("9"),
            ),
        ]
        self.calls = 0

    def get_pm_account_snapshot(self) -> PortfolioSnapshot:
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


class FakeMarketDataService:
    def get_recent_klines(self, symbol: str, interval: str, limit: int) -> list[list[str]]:
        if symbol == "BTCUSDT":
            return [
                ["0", "60000", "61000", "59000", "60500", "12", "0", "720000"],
                ["0", "60500", "61500", "60000", "61000", "15", "0", "915000"],
            ]
        if symbol == "ETHUSDT":
            return [
                ["0", "3000", "3060", "2970", "3040", "50", "0", "152000"],
                ["0", "3040", "3080", "3020", "3070", "55", "0", "168850"],
            ]
        return [
            ["0", "150", "151", "149", "150", "0", "0", "0"],
            ["0", "150", "150", "150", "150", "0", "0", "0"],
        ]

    def get_premium_index(self, symbol: str) -> dict:
        payloads = {
            "BTCUSDT": {"lastFundingRate": "0.0001"},
            "ETHUSDT": {"lastFundingRate": "0.0002"},
            "SOLUSDT": {"lastFundingRate": "0.0020"},
        }
        return payloads[symbol]


class FakeExtremeMarketDataService:
    def get_recent_klines(self, symbol: str, interval: str, limit: int) -> list[list[str]]:
        if symbol == "BTCUSDT":
            return [
                ["0", "60000", "60500", "59800", "60300", "10000", "0", "50000000"],
                ["0", "60300", "60800", "60100", "60600", "9000", "0", "45000000"],
            ]
        if symbol == "ETHUSDT":
            return [
                ["0", "3000", "3030", "2980", "3010", "1000", "0", "5000000"],
                ["0", "3010", "3040", "3000", "3030", "900", "0", "4500000"],
            ]
        return [
            ["0", "150", "152", "149", "151", "800", "0", "4000000"],
            ["0", "151", "153", "150", "152", "700", "0", "3500000"],
        ]

    def get_premium_index(self, symbol: str) -> dict:
        payloads = {
            "BTCUSDT": {"lastFundingRate": "0.0100"},
            "ETHUSDT": {"lastFundingRate": "0.0010"},
            "SOLUSDT": {"lastFundingRate": "0.0008"},
        }
        return payloads[symbol]


class FailingAccountService:
    def get_pm_account_snapshot(self) -> PortfolioSnapshot:
        raise RuntimeError("account snapshot unavailable")


class LiveMarketCycleInputProviderTests(unittest.TestCase):
    def test_provider_builds_rows_snapshot_and_drawdown(self) -> None:
        provider = LiveMarketCycleInputProvider(
            account_service=FakeAccountService(),
            market_data_service=FakeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="5m",
            kline_limit=2,
        )

        first_batch = provider()
        second_batch = provider()

        self.assertEqual(len(first_batch), 1)
        self.assertEqual(first_batch[0].snapshot.account_equity, Decimal("1000"))
        self.assertEqual(first_batch[0].current_drawdown, Decimal("0"))
        self.assertEqual(len(first_batch[0].rows), 3)
        self.assertEqual(first_batch[0].rows[0]["symbol"], "BTCUSDT")
        self.assertEqual(first_batch[0].rows[0]["close"], Decimal("61000"))
        self.assertFalse(first_batch[0].rows[0]["blocked"])
        self.assertTrue(second_batch[0].current_drawdown > Decimal("0"))
        sol_row = next(row for row in first_batch[0].rows if row["symbol"] == "SOLUSDT")
        self.assertTrue(sol_row["blocked"])

    def test_provider_normalizes_liquidity_funding_and_margin_scores(self) -> None:
        provider = LiveMarketCycleInputProvider(
            account_service=FakeAccountService(),
            market_data_service=FakeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="5m",
            kline_limit=2,
        )

        batch = provider()

        for row in batch[0].rows:
            self.assertGreaterEqual(row["liquidity"], Decimal("0"))
            self.assertLessEqual(row["liquidity"], Decimal("1"))
            self.assertGreaterEqual(row["funding"], Decimal("0"))
            self.assertLessEqual(row["funding"], Decimal("1"))
            self.assertGreaterEqual(row["margin"], Decimal("0"))
            self.assertLessEqual(row["margin"], Decimal("1"))

    def test_provider_uses_compressed_normalization_under_extreme_outliers(self) -> None:
        provider = LiveMarketCycleInputProvider(
            account_service=FakeAccountService(),
            market_data_service=FakeExtremeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="5m",
            kline_limit=2,
        )

        batch = provider()
        rows = {row["symbol"]: row for row in batch[0].rows}

        self.assertGreater(rows["ETHUSDT"]["liquidity"], Decimal("0.20"))
        self.assertGreater(rows["SOLUSDT"]["liquidity"], Decimal("0.20"))
        self.assertGreater(rows["ETHUSDT"]["margin"], Decimal("0.20"))
        self.assertGreater(rows["SOLUSDT"]["margin"], Decimal("0.20"))
        self.assertGreater(rows["ETHUSDT"]["funding"], Decimal("0.20"))

    def test_provider_falls_back_to_market_only_rows_when_account_snapshot_unavailable(self) -> None:
        provider = LiveMarketCycleInputProvider(
            account_service=FailingAccountService(),
            market_data_service=FakeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="5m",
            kline_limit=2,
        )

        batch = provider()

        self.assertEqual(len(batch), 1)
        self.assertIsNone(batch[0].snapshot)
        self.assertEqual(batch[0].current_drawdown, Decimal("0"))
        self.assertEqual(len(batch[0].rows), 3)
        self.assertEqual(batch[0].rows[0]["symbol"], "BTCUSDT")

    def test_provider_can_use_virtual_snapshot_provider_without_touching_account_service(self) -> None:
        class StrictAccountService:
            def get_pm_account_snapshot(self) -> PortfolioSnapshot:
                raise AssertionError("real account snapshot should not be called")

        snapshots = iter(
            [
                PortfolioSnapshot(
                    account_equity=Decimal("10000"),
                    available_balance=Decimal("10000"),
                    uni_mmr=Decimal("99999999"),
                ),
                PortfolioSnapshot(
                    account_equity=Decimal("10020"),
                    available_balance=Decimal("10015"),
                    uni_mmr=Decimal("99999999"),
                ),
            ]
        )
        provider = LiveMarketCycleInputProvider(
            account_service=StrictAccountService(),
            market_data_service=FakeMarketDataService(),
            candidate_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="5m",
            kline_limit=2,
            snapshot_provider=lambda: next(snapshots),
        )

        first_batch = provider()
        second_batch = provider()

        self.assertEqual(first_batch[0].snapshot.account_equity, Decimal("10000"))
        self.assertEqual(first_batch[0].current_drawdown, Decimal("0"))
        self.assertEqual(second_batch[0].snapshot.available_balance, Decimal("10015"))
        self.assertEqual(second_batch[0].current_drawdown, Decimal("0"))


class BootstrapLiveMarketCycleInputProviderTests(unittest.TestCase):
    def test_build_live_market_cycle_input_provider_returns_callable(self) -> None:
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

            provider = build_live_market_cycle_input_provider(config_path=env_file)

            self.assertTrue(callable(provider))


if __name__ == "__main__":
    unittest.main()
