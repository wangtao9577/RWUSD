import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


@dataclass(slots=True, frozen=True)
class RestResponse:
    payload: dict
    status_code: int

    def json(self) -> dict:
        return self.payload


class BinanceRestClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        time_provider: Callable[[], int] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._time_provider = time_provider or self._current_timestamp_ms

    def build_request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = False,
        timestamp: int | None = None,
    ) -> Request:
        prepared = dict(params or {})
        if signed:
            prepared["timestamp"] = (
                timestamp if timestamp is not None else self._time_provider()
            )

        query = urlencode(prepared)
        if signed:
            signature = self._sign(query)
            query = urlencode({**prepared, "signature": signature})

        suffix = f"?{query}" if query else ""
        if _is_absolute_url(path):
            url = f"{path}{suffix}"
        else:
            url = f"{self._base_url}{path}{suffix}"
        return Request(
            url=url,
            headers={"X-MBX-APIKEY": self._api_key},
            method=method,
        )

    def get(
        self,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> RestResponse:
        return self._request("GET", path=path, params=params, signed=signed)

    def post(
        self,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> RestResponse:
        return self._request("POST", path=path, params=params, signed=signed)

    def put(
        self,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> RestResponse:
        return self._request("PUT", path=path, params=params, signed=signed)

    def delete(
        self,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> RestResponse:
        return self._request("DELETE", path=path, params=params, signed=signed)

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> RestResponse:
        request = self.build_request(
            method=method,
            path=path,
            params=params,
            signed=signed,
        )
        with urlopen(request, timeout=10.0) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            return RestResponse(payload=payload, status_code=response.status)

    def _sign(self, payload: str) -> str:
        return hmac.new(
            self._api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000)


def _is_absolute_url(path: str) -> bool:
    parsed = urlsplit(path)
    return bool(parsed.scheme and parsed.netloc)
