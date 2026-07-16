import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.infra.sim_config_store import build_config_status, save_simulation_config


class SimConfigServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        request_handler_class,
        *,
        env_file: Path,
        page_dir: Path,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.env_file = env_file
        self.page_dir = page_dir


class SimConfigRequestHandler(BaseHTTPRequestHandler):
    server: SimConfigServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config-status":
            self._write_json(
                HTTPStatus.OK,
                build_config_status(env_file=self.server.env_file),
            )
            return
        if parsed.path in {"/", "/index.html"}:
            self._write_html(self.server.page_dir / "index.html")
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/config":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            payload = self._read_json_body()
            status = save_simulation_config(
                env_file=self.server.env_file,
                api_key=str(payload.get("api_key", "")),
                api_secret=str(payload.get("api_secret", "")),
            )
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        except ValueError as error:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "message": "simulation config saved",
                "status": status,
            },
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("json body must be an object")
        return payload

    def _write_html(self, path: Path) -> None:
        if not path.exists():
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "page not found"})
            return
        body = path.read_text(encoding="utf-8").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_sim_config_server(
    *,
    host: str,
    port: int,
    env_file: str | Path,
    page_dir: str | Path,
) -> SimConfigServer:
    return SimConfigServer(
        (host, port),
        SimConfigRequestHandler,
        env_file=Path(env_file),
        page_dir=Path(page_dir),
    )
