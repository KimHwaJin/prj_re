"""One-time deployment command: PYTHONPATH=src python -m bootstrap_admin."""
import argparse
import asyncio
from pathlib import Path
import sys

from fastapi import HTTPException

from service_settings import configure, load_settings

# Until package separation, root app.py must not shadow the src/app package.
_SRC = str(Path(__file__).resolve().parent)
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)


async def bootstrap(user_id: str, user_name: str):
    from app.core.database import close_database, get_session_factory
    from app.core.enums import UserRole
    from app.schemas.common.user_schema import UserCreate
    from app.services.user_service import UserService
    import app.models.common  # Register FK models before ORM use.
    payload = UserCreate(user_id=user_id, user_name=user_name, role=UserRole.ADMIN)
    try:
        async with get_session_factory()() as db:
            try:
                return await UserService.bootstrap_admin(db, payload)
            except BaseException:
                await db.rollback()
                raise
    finally:
        await close_database()


def main():
    parser = argparse.ArgumentParser(description="Create the first administrator and default project once")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--user-name", required=True)
    parser.add_argument("--env", choices=("dev", "stg", "prd"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-env-file", type=Path)
    args = parser.parse_args()
    configure(load_settings(profile=args.env, config_path=args.config, dotenv_path=args.local_env_file))
    try:
        user = asyncio.run(bootstrap(args.user_id, args.user_name))
    except HTTPException as exc:
        parser.exit(1, str(exc.detail) + "\n")
    print(user.model_dump_json())


if __name__ == "__main__":
    main()
