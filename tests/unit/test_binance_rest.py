from urllib.parse import parse_qs, urlparse
import unittest
from urllib.parse import parse_qs, urlparse

from src.exchange.binance_rest import BinanceRestClient


class BinanceRestClientTests(unittest.TestCase):
    def test_build_request_adds_signature_for_signed_calls(self) -> None:
        client = BinanceRestClient(
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://papi.binance.com",
        )

        request = client.build_request(
            method="GET",
            path="/papi/v1/account",
            params={"recvWindow": 5000},
            signed=True,
            timestamp=1700000000000,
        )

        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/papi/v1/account")
        self.assertEqual(query["recvWindow"], ["5000"])
        self.assertEqual(query["timestamp"], ["1700000000000"])
        self.assertIn("signature", query)
        self.assertEqual(request.get_header("X-mbx-apikey"), "test-key")

    def test_build_request_skips_signature_for_public_calls(self) -> None:
        client = BinanceRestClient(
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://papi.binance.com",
        )

        request = client.build_request(
            method="GET",
            path="/papi/v1/time",
            params={"recvWindow": 5000},
            signed=False,
        )

        query = parse_qs(urlparse(request.full_url).query)

        self.assertNotIn("timestamp", query)
        self.assertNotIn("signature", query)

    def test_build_request_uses_absolute_url_without_prefixing_base_url(self) -> None:
        client = BinanceRestClient(
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://papi.binance.com",
        )

        request = client.build_request(
            method="GET",
            path="https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "limit": 2},
            signed=False,
        )

        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "fapi.binance.com")
        self.assertEqual(parsed.path, "/fapi/v1/klines")
        self.assertEqual(query["symbol"], ["BTCUSDT"])
        self.assertEqual(query["limit"], ["2"])


if __name__ == "__main__":
    unittest.main()
