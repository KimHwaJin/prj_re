from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.core.auth import get_current_user_id
from api_service.core.database import get_db
from api_service.core.enums import DeleteYN
from api_service.core.pagination import ListParams, fetch_page, list_params
from api_service.models import ProjectModel
from api_service.schemas.common.api_schema import Page, ProjectResource
from api_service.schemas.common.project_schema import ProjectCreate, ProjectUpdate
from api_service.services.project_service import ProjectService


router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResource, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    project = await ProjectService.create(db, user_id, payload)
    response.headers["Location"] = f"/api/v1/projects/{project.project_id}"
    return ProjectResource.model_validate(project)


@router.get("", response_model=Page[ProjectResource])
async def list_projects(
    params: ListParams = Depends(list_params),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    items, page = await fetch_page(
        db,
        select(ProjectModel).where(
            ProjectModel.user_id == user_id,
            ProjectModel.delete_yn == DeleteYN.N,
        ),
        model=ProjectModel,
        id_name="project_id",
        params=params,
    )
    return {"items": [ProjectResource.model_validate(item) for item in items], "page": page}


@router.get("/{project_id}", response_model=ProjectResource)
async def read_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return ProjectResource.model_validate(await ProjectService.read(db, user_id, project_id))


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



# Explicit project-wide memory sharing uses the same SSO/CSRF and owner boundary.
from typing import Annotated
from fastapi import Header, HTTPException, Query, Path as PathParam
from uuid import uuid4
from service_contracts.project_memory import MemoryChange, MemorySection, MemoryConflict, MemoryLimit
from api_service.schemas.common.project_memory_schema import MemoryPut, MemoryResource, MemoryWriteResult
from api_service.services.project_memory_policy import ProjectMemoryPolicy

@router.get('/{project_id}/memory',response_model=MemoryResource,response_model_exclude_none=True)
async def read_project_memory(project_id:UUID,user_id:UUID=Depends(get_current_user_id),db:AsyncSession=Depends(get_db)):
    return await ProjectMemoryPolicy(db=db).read(user_id,project_id)

async def write_memory(user_id,project_id,section,key,payload,idempotency_key,*,db,delete=False):
    try:
        change=MemoryChange(section=section,key=key,content=payload.content,expected_version=payload.expected_version)
    except ValueError as exc:
        raise HTTPException(422,'Invalid memory topic') from exc
    if idempotency_key is not None and (not idempotency_key.strip() or not 1 <= len(idempotency_key) <= 100):
        raise HTTPException(422,'Invalid Idempotency-Key')
    try:
        return await ProjectMemoryPolicy(db=db).apply(user_id,project_id,[change.model_dump()],
            source_id='user:'+str(user_id)+':'+(idempotency_key or str(uuid4())),source={'kind':'user_edit'},delete=delete)
    except MemoryConflict as exc: raise HTTPException(409,str(exc)) from exc
    except MemoryLimit as exc: raise HTTPException(422,str(exc)) from exc

@router.put('/{project_id}/memory/{section}/{key}',response_model=MemoryWriteResult)
async def put_project_memory(project_id:UUID,section:MemorySection,key:Annotated[str,PathParam(min_length=1,max_length=48,pattern=r"^[a-z][a-z0-9_]*$")],payload:MemoryPut,
                             idempotency_key:str|None=Header(default=None,min_length=1,max_length=100,alias='Idempotency-Key'),
                             user_id:UUID=Depends(get_current_user_id),db:AsyncSession=Depends(get_db)):
    return await write_memory(user_id,project_id,section,key,payload,idempotency_key,db=db)

@router.delete('/{project_id}/memory/{section}/{key}',response_model=MemoryWriteResult)
async def delete_project_memory(project_id:UUID,section:MemorySection,key:Annotated[str,PathParam(min_length=1,max_length=48,pattern=r"^[a-z][a-z0-9_]*$")],expected_version:int=Query(ge=1),
                                idempotency_key:str|None=Header(default=None,min_length=1,max_length=100,alias='Idempotency-Key'),
                                user_id:UUID=Depends(get_current_user_id),db:AsyncSession=Depends(get_db)):
    return await write_memory(user_id,project_id,section,key,MemoryPut(content='deleted',expected_version=expected_version),idempotency_key,db=db,delete=True)
