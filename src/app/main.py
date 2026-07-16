import argparse

from src.app.bootstrap import (
    build_live_sim_runtime,
    build_live_market_runtime,
    build_live_preflight,
    build_live_runtime,
    main,
    run,
)


def cli(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="project2")
    subparsers = parser.add_subparsers(dest="command")

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--env-file", default=".env")

    preflight_parser = subparsers.add_parser("live-preflight")
    preflight_parser.add_argument("--env-file", default=".env")

    runtime_parser = subparsers.add_parser("live-runtime-file")
    runtime_parser.add_argument("--env-file", default=".env")
    runtime_parser.add_argument("--cycle-inputs", default="tmp/live_cycle_inputs.json")
    runtime_parser.add_argument("--max-loops", type=int, default=None)

    live_runtime_parser = subparsers.add_parser("live-runtime")
    live_runtime_parser.add_argument("--env-file", default=".env")
    live_runtime_parser.add_argument("--max-loops", type=int, default=None)

    sim_runtime_parser = subparsers.add_parser("live-sim-runtime")
    sim_runtime_parser.add_argument("--env-file", default=".env")
    sim_runtime_parser.add_argument("--max-loops", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command in (None, "backtest"):
        env_file = getattr(args, "env_file", ".env")
        return run(env_file=env_file)

    if args.command == "live-preflight":
        preflight = build_live_preflight(config_path=args.env_file)
        return preflight()

    if args.command == "live-runtime-file":
        runtime = build_live_runtime(
            config_path=args.env_file,
            cycle_inputs_path=args.cycle_inputs,
        )
        return runtime(max_loops=args.max_loops)

    if args.command == "live-runtime":
        runtime = build_live_market_runtime(config_path=args.env_file)
        return runtime(max_loops=args.max_loops)

    if args.command == "live-sim-runtime":
        runtime = build_live_sim_runtime(config_path=args.env_file)
        return runtime(max_loops=args.max_loops)

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    cli()
