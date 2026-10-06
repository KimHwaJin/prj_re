from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from dtest.api_service.http.dependencies import get_current_user_id
from dtest.infrastructure.database.runtime import get_db
from dtest.api_service.http.pagination import ListParams, list_params
from dtest.contracts.resources.api_schema import Page
from dtest.contracts.resources.project_schema import (
    ProjectCreate,
    ProjectUpdate,
    ProjectSummary,
    ProjectResource,
)
from dtest.application.resources.project_queries import list_project_summaries
from dtest.application.resources.projects import ProjectService
from dtest.contracts.project_memory import MemoryConflict, MemoryLimit
from dtest.contracts.resources.project_memory_schema import (
    MemoryPut,
    MemoryResource,
)
from dtest.application.resources.project_memory import ProjectMemoryPolicy


router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "", response_model=ProjectResource, status_code=status.HTTP_201_CREATED
)
async def create_project(
    payload: ProjectCreate,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    project = await ProjectService.create(db, user_id, payload)
    response.headers["Location"] = f"/api/v1/projects/{project.project_id}"
    return ProjectResource.model_validate(project)


@router.get("", response_model=Page[ProjectSummary])
async def list_projects(
    response: Response,
    params: ListParams = Depends(list_params),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return await list_project_summaries(db, user_id, params)


@router.get("/{project_id}", response_model=ProjectResource)
async def read_project(
    project_id: UUID,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return ProjectResource.model_validate(
        await ProjectService.read(db, user_id, project_id)
    )


@router.patch("/{project_id}", response_model=ProjectResource)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    project = await ProjectService.update(db, user_id, project_id, payload)
    return ProjectResource.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await ProjectService.delete(db, user_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# One memory resource per project; same SSO/CSRF and owner boundary as CRUD.


@router.get("/{project_id}/memory", response_model=MemoryResource)
async def read_project_memory(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ProjectMemoryPolicy(db=db).read(user_id, project_id)


async def write_memory(
    user_id, project_id, expected_version, idempotency_key, *, db, content=None
):
    if idempotency_key is not None and not idempotency_key.strip():
        raise HTTPException(422, "Invalid Idempotency-Key")
    source_id = (
        "user:" + str(user_id) + ":" + (idempotency_key or str(uuid4()))
    )
    policy = ProjectMemoryPolicy(db=db)
    try:
        if content is None:
            return await policy.reset(
                user_id, project_id, expected_version, source_id=source_id
            )
        return await policy.replace(
            user_id, project_id, content, expected_version, source_id=source_id
        )
    except MemoryConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except MemoryLimit as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{project_id}/memory", response_model=MemoryResource)
async def put_project_memory(
    project_id: UUID,
    payload: MemoryPut,
    idempotency_key: str | None = Header(
        default=None, min_length=1, max_length=100, alias="Idempotency-Key"
    ),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await write_memory(
        user_id,
        project_id,
        payload.expected_version,
        idempotency_key,
        db=db,
        content=payload.content,
    )


@router.delete("/{project_id}/memory", response_model=MemoryResource)
async def delete_project_memory(
    project_id: UUID,
    expected_version: int = Query(ge=0),
    idempotency_key: str | None = Header(
        default=None, min_length=1, max_length=100, alias="Idempotency-Key"
    ),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await write_memory(
        user_id, project_id, expected_version, idempotency_key, db=db
    )
