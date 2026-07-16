from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.demo_long_cycle import write_demo_report


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="run_rwusd_long_cycle_demo")
    parser.add_argument("--output-dir", default="tmp/rwusd_long_cycle_demo")
    args = parser.parse_args(argv)
    return write_demo_report(Path(args.output_dir))


if __name__ == "__main__":
    report = cli()
    print(json.dumps(report["summary"], ensure_ascii=True, indent=2))
