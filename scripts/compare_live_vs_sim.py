import argparse

from src.infra.live_sim_comparison import write_live_sim_comparison_report


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="compare_live_vs_sim")
    parser.add_argument("--live-summary-path", required=True)
    parser.add_argument("--sim-summary-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--live-snapshot-path")
    parser.add_argument("--sim-snapshot-path")
    args = parser.parse_args(argv)

    return write_live_sim_comparison_report(
        live_summary_path=args.live_summary_path,
        sim_summary_path=args.sim_summary_path,
        output_path=args.output_path,
        live_snapshot_path=args.live_snapshot_path,
        sim_snapshot_path=args.sim_snapshot_path,
    )


if __name__ == "__main__":
    cli()
