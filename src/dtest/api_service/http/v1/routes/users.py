from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import Actor, get_current_actor, require_admin
from dtest.infrastructure.database.runtime import get_db
from dtest.api_service.http.pagination import ListParams, list_params
from dtest.contracts.enums import UserRole
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.user_schema import UserCreate, UserRead, UserMe, UserUpdate, UserSummary, UserListStatus
from dtest.application.resources.users import UserService
from dtest.application.resources.user_queries import list_user_summaries
from dtest.api_service.auth.dependencies import get_login_session
from dtest.infrastructure.redis.login_sessions import LoginSession

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, response: Response,
                      actor: Actor = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await UserService.create(db, actor, payload)
    response.headers["Location"] = f"/api/v1/users/{user.user_id}"
    return user


@router.get("", response_model=Page[UserSummary], summary="관리자 사용자 목록 조회")
async def list_users(
    response: Response,
    params: ListParams = Depends(list_params),
    q: str | None = Query(default=None, min_length=1, max_length=100, description="공개 사용자 ID 또는 표시 이름의 대소문자 무시 부분 검색. %, _, 역슬래시는 문자 그대로 검색합니다."),
    role: UserRole | None = Query(default=None, description="admin 또는 user 정확 일치"),
    account_status: UserListStatus = Query(default="active", alias="status", description="active=활성, deleted=삭제된 사용자만, all=전체. 기본은 active입니다."),
    actor: Actor = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """관리자만 사용자 요약을 조회합니다. GET으로 가입·복구·기본 프로젝트 생성을 하지 않습니다."""
    response.headers["Cache-Control"] = "no-store"
    return await list_user_summaries(db, actor, params, q=q, role=role, status=account_status)


# Register before the public string ID route.
@router.get("/me", response_model=UserMe)
async def read_me(response: Response, actor: Actor = Depends(get_current_actor),
                  db: AsyncSession = Depends(get_db), session: LoginSession = Depends(get_login_session)):
    user = await UserService.read(db, actor, actor.public_user_id)
    response.headers["Cache-Control"] = "no-store"
    return UserMe(**user.model_dump(), csrf_token=session.csrf_token, login_expires_at=session.expires_at)


@router.get("/{user_id}", response_model=UserRead)
async def read_user(user_id: str, response: Response, actor: Actor = Depends(get_current_actor),
                    db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
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
