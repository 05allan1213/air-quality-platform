#!/usr/bin/env python3
"""Start the prepared offline backend on Linux or WSL2."""

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "component", nargs="?", default="all", choices=("all", "web", "worker", "beat", "check")
    )
    parser.add_argument("--dev", action="store_true", help="use Django's development HTTP server")
    parser.add_argument("--env-file", metavar="FILE", help="read a dedicated 0600 host config")
    args = parser.parse_args()
    if args.dev and args.component not in {"all", "web"}:
        parser.error("--dev requires all or web")
    if sys.platform != "linux" or sys.version_info[:2] != (3, 13):
        parser.exit(1, "[s07] Linux/WSL2 with Python 3.13 is required\n")
    for module in ("django", "celery", "psycopg", "redis", "gunicorn", "whitenoise"):
        if importlib.util.find_spec(module) is None:
            parser.exit(1, f"[s07] missing dependency {module}; prepare with uv sync --locked\n")
    backend = Path(__file__).resolve().parent / "backend"
    original_environment = os.environ.copy()
    sys.path.insert(0, str(backend))
    from config.host_config import StartupError, config_values

    try:
        values = config_values(original_environment, args.env_file)
        from config.host_boot import launch

        return launch(values, backend, args.component, args.dev)
    except StartupError as exc:
        print(f"[s07] {exc}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"[s07] startup_failed_{type(exc).__name__}", file=sys.stderr)
    except Exception as exc:
        # Driver exceptions include the private endpoint or configuration.
        print(f"[s07] startup_service_error_{type(exc).__name__}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
