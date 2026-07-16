import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.infra.logging import (
    CompositeLogger,
    DatePartitionedJsonlFileLogger,
    InMemoryLogger,
    JsonlFileLogger,
)


class LoggingTests(unittest.TestCase):
    def test_jsonl_file_logger_appends_structured_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "logs" / "runtime.jsonl"
            logger = JsonlFileLogger(log_path)

            logger.log(
                level="INFO",
                message="runtime loop completed",
                event="runtime.loop_completed",
                context={"loop_count": 1, "selected_symbols": ["BTCUSDT"]},
            )

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["event"], "runtime.loop_completed")
            self.assertEqual(payload["context"]["selected_symbols"], ["BTCUSDT"])

    def test_composite_logger_writes_to_memory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "runtime.jsonl"
            memory_logger = InMemoryLogger()
            file_logger = JsonlFileLogger(log_path)
            logger = CompositeLogger([memory_logger, file_logger])

            logger.log(
                level="WARN",
                message="risk decision evaluated",
                event="live.risk_decision",
                context={"reason": "uni_mmr_soft_limit"},
            )

            self.assertEqual(memory_logger.records[0].event, "live.risk_decision")
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["context"]["reason"], "uni_mmr_soft_limit")

    def test_date_partitioned_jsonl_logger_writes_to_daily_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "runtime.jsonl"
            timestamps = iter(
                [
                    datetime(2026, 6, 28, 10, 0, 0),
                    datetime(2026, 6, 29, 10, 0, 0),
                ]
            )
            logger = DatePartitionedJsonlFileLogger(
                log_path,
                now_fn=lambda: next(timestamps),
            )

            logger.log(
                level="INFO",
                message="loop one",
                event="runtime.loop_completed",
                context={"loop_count": 1},
            )
            logger.log(
                level="INFO",
                message="loop two",
                event="runtime.loop_completed",
                context={"loop_count": 2},
            )

            first_daily = Path(tmp_dir) / "runtime-2026-06-28.jsonl"
            second_daily = Path(tmp_dir) / "runtime-2026-06-29.jsonl"
            self.assertTrue(first_daily.exists())
            self.assertTrue(second_daily.exists())
            self.assertEqual(
                json.loads(first_daily.read_text(encoding="utf-8").splitlines()[0])["context"]["loop_count"],
                1,
            )
            self.assertEqual(
                json.loads(second_daily.read_text(encoding="utf-8").splitlines()[0])["context"]["loop_count"],
                2,
            )


if __name__ == "__main__":
    unittest.main()
