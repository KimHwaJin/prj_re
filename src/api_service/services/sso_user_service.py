"""Corporate employee to existing internal UUID; no automatic administrator privileges."""
from fastapi import HTTPException
from pydantic import ValidationError

from api_service.core.database import short_session
from api_service.core.enums import DeleteYN, UserRole
from api_service.core.user_identity import normalize_user_id
from api_service.repositories.user_repository import UserRepository
from api_service.schemas.common.user_schema import UserCreate
from api_service.services.user_service import UserService
from service_auth.sso.contracts import VerifiedEmployee


class SsoUserDirectory:
    def __init__(self, *, auto_register: bool, session_factory=None):
        self.auto_register, self.session_factory = auto_register, session_factory

    async def bind(self, employee: VerifiedEmployee) -> str:
        try:
            public_id = normalize_user_id(employee.employee_id)
        except ValueError:
            raise HTTPException(502, "Corporate employee ID is not compatible with the user ID contract.") from None
        async with short_session(self.session_factory) as db:
            # Serialize provisioning and account deletion/role updates under the same existing lock.
            await UserService._management_lock(db)
            existing = await UserRepository.get_by_public_id(db, public_id, for_update=True)
            if existing is not None:
                if existing.delete_yn != DeleteYN.N:
                    raise HTTPException(403, "The service account is inactive.")
                internal_id = str(existing.user_id)
                await db.commit()
                return internal_id  # Preserve name, role, default project and all foreign keys.
            if not self.auto_register:
                raise HTTPException(403, "The employee has not been registered in this service.")
            try:
                payload = UserCreate(user_id=public_id, user_name=employee.display_name, role=UserRole.USER)
            except ValidationError:
                raise HTTPException(502, "Invalid corporate employee display name.") from None
            user = await UserService._insert(db, payload)
            internal_id = str(user.user_id)
            await db.commit()  # User, default Project and OWNER membership are one transaction.
            return internal_id
