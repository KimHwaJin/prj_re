from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.auth import Actor, get_current_actor, require_admin
from api_service.core.database import get_db
from api_service.schemas.common.user_schema import UserCreate, UserRead, UserUpdate
from api_service.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, response: Response,
                      actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await UserService.create(db, actor, payload)
    response.headers["Location"] = f"/api/v1/users/{user.user_id}"
    return user


# Register before the public string ID route.
@router.get("/me", response_model=UserRead)
async def read_me(actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_db)):
    return await UserService.read(db, actor, actor.public_user_id)


@router.get("/{user_id}", response_model=UserRead)
async def read_user(user_id: str, actor: Actor = Depends(get_current_actor),
                    db: AsyncSession = Depends(get_db)):
    return await UserService.read(db, actor, user_id)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(user_id: str, payload: UserUpdate, actor: Actor = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    return await UserService.update(db, actor, user_id, payload)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, actor: Actor = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    await UserService.delete(db, actor, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
