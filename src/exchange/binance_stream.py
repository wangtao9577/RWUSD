import json
import time
from collections.abc import Callable, Iterable
from decimal import Decimal

from src.exchange.binance_rest import BinanceRestClient


def derive_user_stream_ws_base_url(rest_base_url: str) -> str:
    normalized = rest_base_url.strip().lower()
    if "testnet" in normalized:
        return "wss://stream.binancefuture.com/ws"
    return "wss://fstream.binance.com/ws"


class BinanceUserStreamEventSource:
    def __init__(
        self,
        ws_base_url: str,
        connect_fn: Callable | None = None,
    ) -> None:
        self._ws_base_url = ws_base_url.rstrip("/")
        self._connect_fn = connect_fn

    def __call__(self, listen_key: str):
        connect_fn = self._connect_fn or self._default_connect
        url = f"{self._ws_base_url}/{listen_key}"
        with connect_fn(url) as websocket:
            for message in websocket:
                yield json.loads(message)

    def _default_connect(self, url: str):
        from websockets.sync.client import connect

        return connect(url)


class BinanceStreamClient:
    def __init__(self, rest: BinanceRestClient) -> None:
        self._rest = rest
        self.listen_key: str | None = None

    def start_user_stream(self) -> dict:
        payload = self._rest.post("/papi/v1/listenKey").json()
        self.listen_key = payload.get("listenKey", self.listen_key)
        return payload

    def keepalive_user_stream(self) -> dict:
        return self._rest.put("/papi/v1/listenKey").json()

    def close_user_stream(self) -> dict:
        payload = self._rest.delete("/papi/v1/listenKey").json()
        self.listen_key = None
        return payload

    def run_user_stream_loop(
        self,
        event_source: Callable[[str], Iterable[dict]],
        handler: Callable[[dict], object | None],
        keepalive_every: int = 50,
        max_events: int | None = None,
        retry_attempts: int = 0,
        retry_backoff_seconds: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
        sleep_fn: Callable[[float], object] | None = None,
    ) -> int:
        consumed = 0
        attempts_remaining = retry_attempts
        current_backoff = retry_backoff_seconds
        sleep = sleep_fn or time.sleep
        while True:
            payload = self.start_user_stream()
            listen_key = payload.get("listenKey", self.listen_key)
            if listen_key is None:
                raise ValueError("listen_key_missing")

            try:
                for event in event_source(listen_key):
                    handler(event)
                    consumed += 1

                    if keepalive_every > 0 and consumed % keepalive_every == 0:
                        self.keepalive_user_stream()

                    if max_events is not None and consumed >= max_events:
                        return consumed
                return consumed
            except Exception:
                if attempts_remaining <= 0:
                    raise
                attempts_remaining -= 1
                if current_backoff > 0:
                    sleep(current_backoff)
                current_backoff *= retry_backoff_multiplier
            finally:
                self.close_user_stream()
        return consumed

    def parse_user_stream_event(self, payload: dict) -> dict | None:
        if payload.get("e") != "ORDER_TRADE_UPDATE":
            return None

        order = payload.get("o", {})
        status = order.get("X")
        if status != "FILLED":
            if status == "PARTIALLY_FILLED":
                return self._order_execution_event(
                    event_type="order_partially_filled",
                    order=order,
                )
            if status in {"REJECTED", "CANCELED", "EXPIRED"}:
                event = {
                    "event_type": "order_failed",
                    "symbol": order.get("s"),
                    "position_side": order.get("ps"),
                    "status": status,
                }
                if order.get("i") is not None:
                    event["order_id"] = str(order["i"])
                return event
            return None

        return self._order_execution_event(event_type="order_filled", order=order)

    def _order_execution_event(self, event_type: str, order: dict) -> dict:
        event = {
            "event_type": event_type,
            "symbol": order.get("s"),
            "position_side": order.get("ps"),
        }
        if order.get("i") is not None:
            event["order_id"] = str(order["i"])
        if order.get("z") is not None:
            event["cumulative_filled_quantity"] = Decimal(str(order["z"]))
        return event
