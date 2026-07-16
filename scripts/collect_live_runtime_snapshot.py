import argparse

from src.infra.simulation_report import write_runtime_summary


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="collect_live_runtime_snapshot")
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args(argv)
    return write_runtime_summary(
        log_path=args.log_path,
        output_path=args.output_path,
    )


if __name__ == "__main__":
    cli()
