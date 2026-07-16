import tempfile
import unittest
from pathlib import Path

from src.infra.sim_config_store import build_config_status, save_simulation_config


class SimConfigStoreTests(unittest.TestCase):
    def test_save_simulation_config_overwrites_credentials_and_preserves_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.simulation"
            env_file.write_text(
                "\n".join(
                    [
                        "BINANCE_API_KEY=old-key-value-1234567890",
                        "BINANCE_API_SECRET=old-secret-value-1234567890",
                        "BINANCE_BASE_URL=https://papi.binance.com",
                        "CANDIDATE_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT",
                        "TARGET_NOTIONAL=1500",
                    ]
                ),
                encoding="utf-8",
            )

            save_simulation_config(
                env_file=env_file,
                api_key="new-api-key-12345",
                api_secret="new-api-secret-67890",
            )

            content = env_file.read_text(encoding="utf-8")
            self.assertIn("BINANCE_API_KEY=new-api-key-12345", content)
            self.assertIn("BINANCE_API_SECRET=new-api-secret-67890", content)
            self.assertIn("TARGET_NOTIONAL=1500", content)
            self.assertIn("LIVE_DRY_RUN=true", content)
            self.assertIn("LIVE_LOG_PATH=tmp/server/live_runtime.jsonl", content)

    def test_save_simulation_config_rejects_obviously_invalid_short_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env.simulation"

            with self.assertRaisesRegex(ValueError, "api_key looks invalid"):
                save_simulation_config(
                    env_file=env_file,
                    api_key="short-key",
                    api_secret="valid-secret-value-1234567890",
                )

            with self.assertRaisesRegex(ValueError, "api_secret looks invalid"):
                save_simulation_config(
                    env_file=env_file,
                    api_key="valid-api-key-value-1234567890",
                    api_secret="short-secret",
                )

    def test_build_config_status_masks_secret_and_key_preview(self) -> None:
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

            status = build_config_status(env_file=env_file)

            self.assertTrue(status["api_key_configured"])
            self.assertTrue(status["api_secret_configured"])
            self.assertEqual(status["api_key_masked"], "abc***45")
            self.assertEqual(
                status["candidate_symbols"],
                ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            )
            self.assertEqual(status["live_log_path"], "tmp/server/live_runtime.jsonl")
            self.assertNotIn("api_secret", status)


if __name__ == "__main__":
    unittest.main()
