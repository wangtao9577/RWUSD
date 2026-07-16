import importlib.util
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from src.app.sim_config_server import build_sim_config_server


def _load_runner_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_sim_config_server.py"
    spec = importlib.util.spec_from_file_location("run_sim_config_server_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SimConfigServerTests(unittest.TestCase):
    def test_get_config_status_returns_masked_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.simulation"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=abcdef12345",
                        "BINANCE_API_SECRET=super-secret-value",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "LIVE_DRY_RUN=true",
                        "LIVE_LOG_PATH=tmp/server/live_runtime.jsonl",
                    ]
                ),
                encoding="utf-8",
            )
            page_dir = Path(tmp_dir) / "page"
            page_dir.mkdir()
            (page_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

            server, thread = self._start_server(env_file=env_file, page_dir=page_dir)
            try:
                response = self._request_json(
                    method="GET",
                    port=server.server_address[1],
                    path="/api/config-status",
                )
            finally:
                self._stop_server(server, thread)

            self.assertEqual(response["api_key_masked"], "abc***45")
            self.assertTrue(response["api_key_configured"])
            self.assertTrue(response["api_secret_configured"])
            self.assertNotIn("api_secret", response)

    def test_post_config_saves_credentials_and_returns_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.simulation"
            page_dir = Path(tmp_dir) / "page"
            page_dir.mkdir()
            (page_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

            server, thread = self._start_server(env_file=env_file, page_dir=page_dir)
            try:
                response = self._request_json(
                    method="POST",
                    port=server.server_address[1],
                    path="/api/config",
                    body={
                        "api_key": "posted-key-12345",
                        "api_secret": "posted-secret-67890",
                    },
                )
            finally:
                self._stop_server(server, thread)

            self.assertTrue(response["ok"])
            self.assertTrue(response["status"]["api_key_configured"])
            self.assertTrue(response["status"]["api_secret_configured"])
            self.assertIn("BINANCE_API_KEY=posted-key-12345", env_file.read_text(encoding="utf-8"))

    def test_post_config_rejects_blank_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.simulation"
            page_dir = Path(tmp_dir) / "page"
            page_dir.mkdir()
            (page_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

            server, thread = self._start_server(env_file=env_file, page_dir=page_dir)
            try:
                status_code, payload = self._request_json_with_status(
                    method="POST",
                    port=server.server_address[1],
                    path="/api/config",
                    body={
                        "api_key": "   ",
                        "api_secret": "posted-secret-67890",
                    },
                )
            finally:
                self._stop_server(server, thread)

            self.assertEqual(status_code, 400)
            self.assertIn("must not be blank", payload["error"])

    def test_run_sim_config_server_cli_parses_bind_host_port_and_env_file(self) -> None:
        module = _load_runner_module()
        captured: dict[str, object] = {}

        class FakeServer:
            def serve_forever(self) -> None:
                captured["serve_forever_called"] = True

            def server_close(self) -> None:
                captured["server_close_called"] = True

        def fake_build_server(*, host, port, env_file, page_dir):
            captured["host"] = host
            captured["port"] = port
            captured["env_file"] = str(env_file)
            captured["page_dir"] = str(page_dir)
            return FakeServer()

        result = module.cli(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "18081",
                "--env-file",
                "tmp/.env.simulation",
                "--page-dir",
                "tmp/server_sim_control",
            ],
            build_server_fn=fake_build_server,
            serve_forever=False,
        )

        self.assertEqual(captured["host"], "127.0.0.1")
        self.assertEqual(captured["port"], 18081)
        self.assertEqual(captured["env_file"], "tmp/.env.simulation")
        self.assertEqual(captured["page_dir"], "tmp/server_sim_control")
        self.assertEqual(result["host"], "127.0.0.1")
        self.assertEqual(result["port"], 18081)

    def _start_server(self, *, env_file: Path, page_dir: Path):
        server = build_sim_config_server(
            host="127.0.0.1",
            port=0,
            env_file=env_file,
            page_dir=page_dir,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _stop_server(self, server, thread) -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def _request_json(self, *, method: str, port: int, path: str, body: dict | None = None):
        status_code, payload = self._request_json_with_status(
            method=method,
            port=port,
            path=path,
            body=body,
        )
        self.assertEqual(status_code, 200)
        return payload

    def _request_json_with_status(
        self,
        *,
        method: str,
        port: int,
        path: str,
        body: dict | None = None,
    ):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        raw_body = None if body is None else json.dumps(body)
        headers = {"Content-Type": "application/json"} if raw_body is not None else {}
        connection.request(method, path, body=raw_body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload


if __name__ == "__main__":
    unittest.main()
