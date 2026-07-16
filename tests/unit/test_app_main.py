import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import main as app_main


class AppMainTests(unittest.TestCase):
    def test_cli_backtest_dispatches_to_bootstrap_run(self) -> None:
        with patch("src.app.main.run", return_value="backtest-ok") as mocked_run:
            result = app_main.cli(["backtest", "--env-file", "sample.env"])

        self.assertEqual(result, "backtest-ok")
        mocked_run.assert_called_once_with(env_file="sample.env")

    def test_cli_live_preflight_dispatches_to_preflight_builder(self) -> None:
        fake_preflight = lambda: "preflight-ok"
        with patch(
            "src.app.main.build_live_preflight",
            return_value=fake_preflight,
        ) as mocked_builder:
            result = app_main.cli(["live-preflight", "--env-file", "sample.env"])

        self.assertEqual(result, "preflight-ok")
        mocked_builder.assert_called_once_with(config_path="sample.env")

    def test_cli_live_runtime_file_dispatches_to_runtime_builder(self) -> None:
        fake_runtime = lambda max_loops=None: {
            "status": "started",
            "max_loops": max_loops,
        }
        with patch(
            "src.app.main.build_live_runtime",
            return_value=fake_runtime,
        ) as mocked_builder:
            result = app_main.cli(
                [
                    "live-runtime-file",
                    "--env-file",
                    "sample.env",
                    "--cycle-inputs",
                    "cycles.json",
                    "--max-loops",
                    "3",
                ]
            )

        self.assertEqual(result, {"status": "started", "max_loops": 3})
        mocked_builder.assert_called_once_with(
            config_path="sample.env",
            cycle_inputs_path="cycles.json",
        )

    def test_cli_live_runtime_dispatches_to_market_runtime_builder(self) -> None:
        fake_runtime = lambda max_loops=None: {
            "status": "started",
            "max_loops": max_loops,
        }
        with patch(
            "src.app.main.build_live_market_runtime",
            return_value=fake_runtime,
        ) as mocked_builder:
            result = app_main.cli(
                [
                    "live-runtime",
                    "--env-file",
                    "sample.env",
                    "--max-loops",
                    "2",
                ]
            )

        self.assertEqual(result, {"status": "started", "max_loops": 2})
        mocked_builder.assert_called_once_with(config_path="sample.env")

    def test_cli_live_sim_runtime_dispatches_to_sim_runtime_builder(self) -> None:
        fake_runtime = lambda max_loops=None: {
            "status": "started",
            "max_loops": max_loops,
        }
        with patch(
            "src.app.main.build_live_sim_runtime",
            return_value=fake_runtime,
        ) as mocked_builder:
            result = app_main.cli(
                [
                    "live-sim-runtime",
                    "--env-file",
                    "sample.env",
                    "--max-loops",
                    "2",
                ]
            )

        self.assertEqual(result, {"status": "started", "max_loops": 2})
        mocked_builder.assert_called_once_with(config_path="sample.env")

    def test_cli_defaults_to_backtest_when_no_command_is_provided(self) -> None:
        with patch("src.app.main.run", return_value="backtest-default") as mocked_run:
            result = app_main.cli([])

        self.assertEqual(result, "backtest-default")
        mocked_run.assert_called_once_with(env_file=".env")


if __name__ == "__main__":
    unittest.main()
