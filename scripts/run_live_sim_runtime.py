import argparse

from src.app.bootstrap import build_live_preflight, build_live_sim_runtime


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="run_live_sim_runtime")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--max-loops", type=int, default=None)
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args(argv)

    preflight_result = None
    if not args.skip_preflight:
        preflight = build_live_preflight(config_path=args.env_file)
        preflight_result = preflight()

    runtime = build_live_sim_runtime(config_path=args.env_file)
    runtime_result = runtime(max_loops=args.max_loops)
    outcome = _extract_runtime_outcome(runtime_result)
    return {
        "preflight": preflight_result,
        "runtime": runtime_result,
        "outcome": outcome,
    }


def _extract_runtime_outcome(result: object):
    if hasattr(result, "outcome"):
        return getattr(result, "outcome")
    if isinstance(result, dict):
        return result.get("outcome")
    return None


if __name__ == "__main__":
    cli()
