from decimal import Decimal
import unittest

from src.domain.models import PortfolioSnapshot
from src.exchange.binance_account import (
    BinanceAccountService,
    UmHedgePosition,
    build_pm_risk_snapshot,
    parse_pm_account,
)


class FakeRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, bool]] = []

    def post(self, path: str, params: dict | None = None, signed: bool = False):
        self.calls.append(("post", path, params or {}, signed))

        class Response:
            def __init__(self, payload: dict) -> None:
                self._payload = payload

            def json(self) -> dict:
                return self._payload

        return Response({"path": path, "params": params or {}, "signed": signed})


class BinanceAccountParsingTests(unittest.TestCase):
    def test_parse_pm_account_maps_unimmr_and_balances(self) -> None:
        payload = {
            "uniMMR": "13.42",
            "accountEquity": "1520.55",
            "availableBalance": "640.10",
        }

        snapshot = parse_pm_account(payload)

        self.assertEqual(snapshot.uni_mmr, Decimal("13.42"))
        self.assertEqual(snapshot.account_equity, Decimal("1520.55"))
        self.assertEqual(snapshot.available_balance, Decimal("640.10"))

    def test_parse_pm_account_falls_back_to_total_available_balance(self) -> None:
        payload = {
            "uniMMR": "99999999",
            "accountEquity": "9.98488121",
            "totalAvailableBalance": "9.95990515",
        }

        snapshot = parse_pm_account(payload)

        self.assertEqual(snapshot.uni_mmr, Decimal("99999999"))
        self.assertEqual(snapshot.account_equity, Decimal("9.98488121"))
        self.assertEqual(snapshot.available_balance, Decimal("9.95990515"))

    def test_build_pm_risk_snapshot_aggregates_positions_by_underlying(self) -> None:
        snapshot = build_pm_risk_snapshot(
            account_snapshot=PortfolioSnapshot(
                account_equity=Decimal("1000"),
                available_balance=Decimal("500"),
                uni_mmr=Decimal("8"),
            ),
            positions=[
                UmHedgePosition(
                    symbol="ETHUSDT",
                    long_notional=Decimal("600"),
                    short_notional=Decimal("450"),
                ),
                UmHedgePosition(
                    symbol="ETHUSDC",
                    long_notional=Decimal("100"),
                    short_notional=Decimal("175"),
                ),
                UmHedgePosition(
                    symbol="BTCUSDT",
                    long_notional=Decimal("300"),
                    short_notional=Decimal("300"),
                ),
            ],
        )

        self.assertEqual(snapshot.total_abs_notional, Decimal("1925"))
        self.assertEqual(snapshot.total_net_notional, Decimal("75"))
        self.assertEqual(snapshot.single_symbol_net_notional, Decimal("75"))

    def test_account_service_exposes_spot_to_pm_transfer_and_rwusd_redeem_surface(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        transfer_result = service.transfer_spot_to_pm(asset="USDT", amount="75")
        redeem_result = service.redeem_rwusd(amount="50")

        self.assertEqual(
            rest.calls,
            [
                (
                    "post",
                    "/sapi/v1/asset/transfer",
                    {"asset": "USDT", "amount": "75"},
                    True,
                ),
                (
                    "post",
                    "/sapi/v1/rwusd/redeem",
                    {"amount": "50"},
                    True,
                ),
            ],
        )
        self.assertEqual(transfer_result["path"], "/sapi/v1/asset/transfer")
        self.assertEqual(redeem_result["path"], "/sapi/v1/rwusd/redeem")


if __name__ == "__main__":
    unittest.main()
