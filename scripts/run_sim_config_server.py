import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.sim_config_server import build_sim_config_server


def cli(
    argv: list[str] | None = None,
    *,
    build_server_fn=build_sim_config_server,
    serve_forever: bool = True,
) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="run_sim_config_server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--env-file", default=".env.simulation")
    parser.add_argument("--page-dir", default="server_sim_control")
    args = parser.parse_args(argv)

    server = build_server_fn(
        host=args.host,
        port=args.port,
        env_file=args.env_file,
        page_dir=args.page_dir,
    )

    result = {
        "host": args.host,
        "port": args.port,
        "env_file": str(args.env_file),
        "page_dir": str(args.page_dir),
    }

    if serve_forever:
        try:
            server.serve_forever()
        finally:
            server.server_close()

    return result


if __name__ == "__main__":
    cli()
