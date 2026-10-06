"""One-time deployment command: PYTHONPATH=src python -m bootstrap_admin."""
import argparse
from pathlib import Path
import asyncio

from dtest.contracts.errors import ApplicationError

from dtest.settings.loader import configure, load_settings


async def bootstrap(user_id: str, user_name: str):
    from dtest.infrastructure.database.runtime import close_database, get_session_factory
    from dtest.contracts.enums import UserRole
    from dtest.contracts.resources.user_schema import UserCreate
    from dtest.application.resources.users import UserService
    import dtest.infrastructure.database.models  # Register FK models before ORM use.
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
    except ApplicationError as exc:
        parser.exit(1, str(exc.detail) + "\n")
    print(user.model_dump_json())


if __name__ == "__main__":
    main()
