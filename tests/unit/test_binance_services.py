import json
from decimal import Decimal
import unittest

from src.exchange.binance_account import BinanceAccountService, UmHedgePosition
from src.exchange.binance_market import BinanceMarketDataService
from src.exchange.binance_stream import (
    BinanceStreamClient,
    BinanceUserStreamEventSource,
    derive_user_stream_ws_base_url,
)
from src.strategy.position_sizing import OrderSizingRule


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, bool]] = []

    def get(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
        self.calls.append(("GET", path, params, signed))
        return FakeResponse({"path": path, "params": params, "signed": signed})

    def post(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
        self.calls.append(("POST", path, params, signed))
        return FakeResponse({"path": path, "params": params, "signed": signed})

    def put(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
        self.calls.append(("PUT", path, params, signed))
        return FakeResponse({"path": path, "params": params, "signed": signed})

    def delete(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
        self.calls.append(("DELETE", path, params, signed))
        return FakeResponse({"path": path, "params": params, "signed": signed})


class BinanceAccountServiceTests(unittest.TestCase):
    def test_get_pm_account_uses_signed_account_endpoint(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        payload = service.get_pm_account()

        self.assertEqual(
            rest.calls[0],
            ("GET", "/papi/v1/account", None, True),
        )
        self.assertTrue(payload["signed"])

    def test_set_um_position_mode_uses_signed_position_mode_endpoint(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        payload = service.set_um_position_mode(dual_side_position=True)

        self.assertEqual(
            rest.calls[0],
            (
                "POST",
                "/papi/v1/um/positionSide/dual",
                {"dualSidePosition": "true"},
                True,
            ),
        )
        self.assertEqual(payload["path"], "/papi/v1/um/positionSide/dual")

    def test_get_um_position_mode_uses_signed_position_mode_query_endpoint(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        payload = service.get_um_position_mode()

        self.assertEqual(
            rest.calls[0],
            ("GET", "/papi/v1/um/positionSide/dual", None, True),
        )
        self.assertEqual(payload["path"], "/papi/v1/um/positionSide/dual")

    def test_place_um_order_uses_signed_order_endpoint_with_position_side(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        payload = service.place_um_order(
            symbol="BTCUSDT",
            side="BUY",
            position_side="LONG",
            order_type="MARKET",
            quantity="0.01",
        )

        self.assertEqual(
            rest.calls[0],
            (
                "POST",
                "/papi/v1/um/order",
                {
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "quantity": "0.01",
                },
                True,
            ),
        )
        self.assertTrue(payload["signed"])

    def test_place_um_order_forwards_optional_limit_params(self) -> None:
        rest = FakeRestClient()
        service = BinanceAccountService(rest)

        payload = service.place_um_order(
            symbol="ETHUSDC",
            side="BUY",
            position_side="LONG",
            order_type="LIMIT",
            quantity="0.625",
            price="1600",
            time_in_force="GTX",
            reduce_only=False,
        )

        self.assertEqual(
            rest.calls[0],
            (
                "POST",
                "/papi/v1/um/order",
                {
                    "symbol": "ETHUSDC",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "LIMIT",
                    "quantity": "0.625",
                    "price": "1600",
                    "timeInForce": "GTX",
                    "reduceOnly": "false",
                },
                True,
            ),
        )
        self.assertEqual(payload["params"]["timeInForce"], "GTX")

    def test_get_symbol_order_sizing_rule_reads_market_filters(self) -> None:
        class ExchangeInfoRestClient(FakeRestClient):
            def get(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("GET", path, params, signed))
                return FakeResponse(
                    {
                        "symbols": [
                            {
                                "symbol": "BTCUSDT",
                                "filters": [
                                    {
                                        "filterType": "LOT_SIZE",
                                        "minQty": "0.001",
                                        "stepSize": "0.001",
                                    },
                                    {
                                        "filterType": "MARKET_LOT_SIZE",
                                        "minQty": "0.001",
                                        "stepSize": "0.001",
                                    },
                                    {
                                        "filterType": "MIN_NOTIONAL",
                                        "notional": "5",
                                    },
                                ],
                            }
                        ]
                    }
                )

        rest = ExchangeInfoRestClient()
        service = BinanceAccountService(rest)

        rule = service.get_symbol_order_sizing_rule("BTCUSDT")

        self.assertEqual(
            rule,
            OrderSizingRule(
                step_size=Decimal("0.001"),
                min_qty=Decimal("0.001"),
                min_notional=Decimal("5"),
            ),
        )
        self.assertEqual(
            rest.calls[0],
            ("GET", "/fapi/v1/exchangeInfo", None, False),
        )

    def test_get_symbol_order_sizing_rule_uses_fapi_domain_for_papi_base_url(self) -> None:
        class ExchangeInfoRestClient(FakeRestClient):
            def __init__(self) -> None:
                super().__init__()
                self._base_url = "https://papi.binance.com"

            def get(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("GET", path, params, signed))
                return FakeResponse(
                    {
                        "symbols": [
                            {
                                "symbol": "BTCUSDT",
                                "filters": [
                                    {
                                        "filterType": "LOT_SIZE",
                                        "minQty": "0.001",
                                        "stepSize": "0.001",
                                    },
                                    {
                                        "filterType": "MIN_NOTIONAL",
                                        "minNotional": "5",
                                    },
                                ],
                            }
                        ]
                    }
                )

        rest = ExchangeInfoRestClient()
        service = BinanceAccountService(rest)

        service.get_symbol_order_sizing_rule("BTCUSDT")

        self.assertEqual(
            rest.calls[0],
            ("GET", "https://fapi.binance.com/fapi/v1/exchangeInfo", None, False),
        )

    def test_get_um_hedge_positions_reads_signed_um_account_and_groups_legs(self) -> None:
        class UmAccountRestClient(FakeRestClient):
            def get(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("GET", path, params, signed))
                return FakeResponse(
                    {
                        "positions": [
                            {
                                "symbol": "BTCUSDT",
                                "positionSide": "LONG",
                                "positionAmt": "0.020",
                                "notional": "1200",
                            },
                            {
                                "symbol": "BTCUSDT",
                                "positionSide": "SHORT",
                                "positionAmt": "-0.020",
                                "notional": "-1195",
                            },
                            {
                                "symbol": "ETHUSDT",
                                "positionSide": "LONG",
                                "positionAmt": "0",
                                "notional": "0",
                            },
                        ]
                    }
                )

        rest = UmAccountRestClient()
        service = BinanceAccountService(rest)

        positions = service.get_um_hedge_positions()

        self.assertEqual(
            positions,
            [
                UmHedgePosition(
                    symbol="BTCUSDT",
                    long_qty=Decimal("0.020"),
                    short_qty=Decimal("0.020"),
                    long_notional=Decimal("1200"),
                    short_notional=Decimal("1195"),
                )
            ],
        )
        self.assertEqual(
            rest.calls[0],
            ("GET", "/papi/v1/um/account", None, True),
        )


class BinanceStreamClientTests(unittest.TestCase):
    def test_user_stream_lifecycle_uses_listen_key_endpoints(self) -> None:
        rest = FakeRestClient()
        client = BinanceStreamClient(rest=rest)

        client.start_user_stream()
        client.keepalive_user_stream()
        client.close_user_stream()

        self.assertEqual(
            rest.calls,
            [
                ("POST", "/papi/v1/listenKey", None, False),
                ("PUT", "/papi/v1/listenKey", None, False),
                ("DELETE", "/papi/v1/listenKey", None, False),
            ],
        )

    def test_parse_order_trade_update_returns_fill_event_for_filled_order(self) -> None:
        client = BinanceStreamClient(rest=FakeRestClient())

        event = client.parse_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "s": "BTCUSDT",
                    "ps": "LONG",
                    "X": "FILLED",
                },
            }
        )

        self.assertEqual(
            event,
            {
                "event_type": "order_filled",
                "symbol": "BTCUSDT",
                "position_side": "LONG",
            },
        )

    def test_parse_order_trade_update_ignores_non_filled_or_unknown_events(self) -> None:
        client = BinanceStreamClient(rest=FakeRestClient())

        open_event = client.parse_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "s": "BTCUSDT",
                    "ps": "LONG",
                    "X": "NEW",
                },
            }
        )
        other_event = client.parse_user_stream_event({"e": "ACCOUNT_UPDATE"})

        self.assertIsNone(open_event)
        self.assertIsNone(other_event)

    def test_parse_order_trade_update_returns_cumulative_partial_fill(self) -> None:
        client = BinanceStreamClient(rest=FakeRestClient())

        event = client.parse_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "s": "BTCUSDT",
                    "ps": "LONG",
                    "i": 42,
                    "z": "0.125",
                    "X": "PARTIALLY_FILLED",
                },
            }
        )

        self.assertEqual(
            event,
            {
                "event_type": "order_partially_filled",
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "order_id": "42",
                "cumulative_filled_quantity": Decimal("0.125"),
            },
        )

    def test_parse_order_trade_update_returns_failure_event_for_terminal_reject(self) -> None:
        client = BinanceStreamClient(rest=FakeRestClient())

        event = client.parse_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "s": "BTCUSDT",
                    "ps": "SHORT",
                    "X": "REJECTED",
                },
            }
        )

        self.assertEqual(
            event,
            {
                "event_type": "order_failed",
                "symbol": "BTCUSDT",
                "position_side": "SHORT",
                "status": "REJECTED",
            },
        )

    def test_run_user_stream_loop_consumes_events_with_keepalive_and_close(self) -> None:
        class ListenKeyRestClient(FakeRestClient):
            def post(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("POST", path, params, signed))
                return FakeResponse({"listenKey": "listen-key-1"})

        rest = ListenKeyRestClient()
        client = BinanceStreamClient(rest=rest)
        handled: list[dict] = []

        count = client.run_user_stream_loop(
            event_source=lambda listen_key: [
                {"event": 1, "listenKey": listen_key},
                {"event": 2, "listenKey": listen_key},
                {"event": 3, "listenKey": listen_key},
            ],
            handler=handled.append,
            keepalive_every=2,
        )

        self.assertEqual(count, 3)
        self.assertEqual(
            handled,
            [
                {"event": 1, "listenKey": "listen-key-1"},
                {"event": 2, "listenKey": "listen-key-1"},
                {"event": 3, "listenKey": "listen-key-1"},
            ],
        )
        self.assertEqual(
            rest.calls,
            [
                ("POST", "/papi/v1/listenKey", None, False),
                ("PUT", "/papi/v1/listenKey", None, False),
                ("DELETE", "/papi/v1/listenKey", None, False),
            ],
        )
        self.assertIsNone(client.listen_key)

    def test_run_user_stream_loop_retries_after_event_source_failure(self) -> None:
        class ListenKeyRestClient(FakeRestClient):
            def __init__(self) -> None:
                super().__init__()
                self.listen_index = 0

            def post(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("POST", path, params, signed))
                self.listen_index += 1
                return FakeResponse({"listenKey": f"listen-key-{self.listen_index}"})

        rest = ListenKeyRestClient()
        client = BinanceStreamClient(rest=rest)
        handled: list[dict] = []
        attempts = {"count": 0}

        def flaky_event_source(listen_key: str):
            attempts["count"] += 1
            if attempts["count"] == 1:
                yield {"event": 1, "listenKey": listen_key}
                raise RuntimeError("stream dropped")
            yield {"event": 2, "listenKey": listen_key}

        count = client.run_user_stream_loop(
            event_source=flaky_event_source,
            handler=handled.append,
            keepalive_every=10,
            retry_attempts=1,
        )

        self.assertEqual(count, 2)
        self.assertEqual(
            handled,
            [
                {"event": 1, "listenKey": "listen-key-1"},
                {"event": 2, "listenKey": "listen-key-2"},
            ],
        )
        self.assertEqual(
            rest.calls,
            [
                ("POST", "/papi/v1/listenKey", None, False),
                ("DELETE", "/papi/v1/listenKey", None, False),
                ("POST", "/papi/v1/listenKey", None, False),
                ("DELETE", "/papi/v1/listenKey", None, False),
            ],
        )

    def test_run_user_stream_loop_uses_exponential_backoff_between_retries(self) -> None:
        class ListenKeyRestClient(FakeRestClient):
            def __init__(self) -> None:
                super().__init__()
                self.listen_index = 0

            def post(self, path: str, params: dict | None = None, signed: bool = False) -> FakeResponse:
                self.calls.append(("POST", path, params, signed))
                self.listen_index += 1
                return FakeResponse({"listenKey": f"listen-key-{self.listen_index}"})

        rest = ListenKeyRestClient()
        client = BinanceStreamClient(rest=rest)
        sleeps: list[float] = []
        attempts = {"count": 0}

        def flaky_event_source(listen_key: str):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError(f"stream dropped {listen_key}")
            yield {"event": 3, "listenKey": listen_key}

        count = client.run_user_stream_loop(
            event_source=flaky_event_source,
            handler=lambda _: None,
            retry_attempts=2,
            retry_backoff_seconds=0.5,
            retry_backoff_multiplier=2.0,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(count, 1)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_user_stream_event_source_builds_ws_url_and_parses_json_messages(self) -> None:
        captured: dict[str, str] = {}

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def __iter__(self):
                yield json.dumps({"e": "ORDER_TRADE_UPDATE", "seq": 1})
                yield json.dumps({"e": "ACCOUNT_UPDATE", "seq": 2})

        def fake_connect(url: str):
            captured["url"] = url
            return FakeConnection()

        source = BinanceUserStreamEventSource(
            ws_base_url="wss://fstream.binance.com/ws",
            connect_fn=fake_connect,
        )

        events = list(source("listen-key-1"))

        self.assertEqual(
            captured["url"],
            "wss://fstream.binance.com/ws/listen-key-1",
        )
        self.assertEqual(
            events,
            [
                {"e": "ORDER_TRADE_UPDATE", "seq": 1},
                {"e": "ACCOUNT_UPDATE", "seq": 2},
            ],
        )

    def test_derive_user_stream_ws_base_url_uses_futures_domains(self) -> None:
        self.assertEqual(
            derive_user_stream_ws_base_url("https://papi.binance.com"),
            "wss://fstream.binance.com/ws",
        )
        self.assertEqual(
            derive_user_stream_ws_base_url("https://testnet.binancefuture.com"),
            "wss://stream.binancefuture.com/ws",
        )


class BinanceMarketDataServiceTests(unittest.TestCase):
    def test_get_recent_klines_uses_futures_kline_endpoint(self) -> None:
        rest = FakeRestClient()
        service = BinanceMarketDataService(rest)

        payload = service.get_recent_klines("BTCUSDT", interval="5m", limit=20)

        self.assertEqual(
            rest.calls[0],
            (
                "GET",
                "/fapi/v1/klines",
                {"symbol": "BTCUSDT", "interval": "5m", "limit": 20},
                False,
            ),
        )
        self.assertEqual(payload["path"], "/fapi/v1/klines")

    def test_get_premium_index_uses_futures_premium_index_endpoint(self) -> None:
        rest = FakeRestClient()
        service = BinanceMarketDataService(rest)

        payload = service.get_premium_index("BTCUSDT")

        self.assertEqual(
            rest.calls[0],
            (
                "GET",
                "/fapi/v1/premiumIndex",
                {"symbol": "BTCUSDT"},
                False,
            ),
        )
        self.assertEqual(payload["path"], "/fapi/v1/premiumIndex")

    def test_get_recent_klines_uses_fapi_domain_for_papi_base_url(self) -> None:
        class FuturesMarketRestClient(FakeRestClient):
            def __init__(self) -> None:
                super().__init__()
                self._base_url = "https://papi.binance.com"

        rest = FuturesMarketRestClient()
        service = BinanceMarketDataService(rest)

        payload = service.get_recent_klines("BTCUSDT", interval="5m", limit=20)

        self.assertEqual(
            rest.calls[0],
            (
                "GET",
                "https://fapi.binance.com/fapi/v1/klines",
                {"symbol": "BTCUSDT", "interval": "5m", "limit": 20},
                False,
            ),
        )
        self.assertEqual(payload["path"], "https://fapi.binance.com/fapi/v1/klines")

    def test_get_premium_index_uses_fapi_domain_for_papi_base_url(self) -> None:
        class FuturesMarketRestClient(FakeRestClient):
            def __init__(self) -> None:
                super().__init__()
                self._base_url = "https://papi.binance.com"

        rest = FuturesMarketRestClient()
        service = BinanceMarketDataService(rest)

        payload = service.get_premium_index("BTCUSDT")

        self.assertEqual(
            rest.calls[0],
            (
                "GET",
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                {"symbol": "BTCUSDT"},
                False,
            ),
        )
        self.assertEqual(payload["path"], "https://fapi.binance.com/fapi/v1/premiumIndex")


if __name__ == "__main__":
    unittest.main()
