import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class LogRecord:
    level: str
    message: str
    event: str = ""
    context: dict = field(default_factory=dict)


class InMemoryLogger:
    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def log(
        self,
        level: str,
        message: str,
        event: str,
        context: dict | None = None,
    ) -> None:
        self.records.append(
            LogRecord(
                level=level,
                message=message,
                event=event,
                context=context or {},
            )
        )


class JsonlFileLogger:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def log(
        self,
        level: str,
        message: str,
        event: str,
        context: dict | None = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = LogRecord(
            level=level,
            message=message,
            event=event,
            context=context or {},
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=True) + "\n")


class DatePartitionedJsonlFileLogger:
    def __init__(
        self,
        path: Path | str,
        now_fn=None,
    ) -> None:
        self._path = Path(path)
        self._now_fn = now_fn or datetime.now

    def log(
        self,
        level: str,
        message: str,
        event: str,
        context: dict | None = None,
    ) -> None:
        record = LogRecord(
            level=level,
            message=message,
            event=event,
            context=context or {},
        )
        target_path = self._daily_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=True) + "\n")

    def _daily_path(self) -> Path:
        stamp = self._now_fn().strftime("%Y-%m-%d")
        return self._path.with_name(f"{self._path.stem}-{stamp}{self._path.suffix}")


class CompositeLogger:
    def __init__(self, loggers: list[object]) -> None:
        self._loggers = loggers

    def log(
        self,
        level: str,
        message: str,
        event: str,
        context: dict | None = None,
    ) -> None:
        for logger in self._loggers:
            logger.log(
                level=level,
                message=message,
                event=event,
                context=context,
            )
