#!/usr/bin/env python3
"""Prepare CRUD/event/checkpoint schemas with the exact same source selection as app.py."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dtest.settings.loader import configure, load_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["local", "dev", "stg", "prd"])
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--local-env-file",
        type=Path,
        help="Legacy dev-only supplementary input",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Resolve targets without connecting or migrating",
    )
    args = parser.parse_args(argv)
    settings = configure(
        load_settings(
            profile=args.env,
            config_path=args.config,
            dotenv_path=args.local_env_file,
        )
    )
    if args.check_config:
        import json

        print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
        return
    from dtest.infrastructure.database.schema import initialize_databases

    initialize_databases(settings)
    print(
        "CRUD/event/checkpoint schemas prepared with the selected "
        "service "
        "YAML."
    )


if __name__ == "__main__":
    main()
