#!/usr/bin/env python3
"""Prepare CRUD/event/checkpoint schemas with the exact same source selection as app.py."""
import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from service_settings import configure, load_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["dev", "stg", "prd"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-env-file", type=Path, help="Legacy dev-only supplementary input")
    parser.add_argument("--check-config", action="store_true", help="Resolve targets without connecting or migrating")
    args = parser.parse_args(argv)
    settings = configure(load_settings(profile=args.env, config_path=args.config, dotenv_path=args.local_env_file))
    if args.check_config:
        import json
        print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
        return
    from alembic import command
    from alembic.config import Config
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from api_service.runs.commands.migrate import main as admit_commands
    for ini in ("alembic.crud.ini", "alembic.ini"):
        command.upgrade(Config(str(ROOT / ini)), "head")
    async def prepare():
        async with AsyncPostgresSaver.from_conn_string(settings.agent.checkpoint_db_uri) as saver:
            await saver.setup()
        await admit_commands()
    asyncio.run(prepare())
    print("CRUD/event/checkpoint schemas prepared with the selected service YAML.")


if __name__ == "__main__":
    main()
