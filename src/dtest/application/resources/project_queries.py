"""Owner-scoped project summary page without ORM or prompt hydration."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Bundle

from dtest.contracts.enums import DeleteYN
from dtest.contracts.pagination import ListParams
from dtest.contracts.resources.project_schema import ProjectSummary
from dtest.infrastructure.database.models.project_model import (
    ProjectModel as Project,
)
from dtest.infrastructure.database.pagination import fetch_page

PROJECT_SUMMARIES = select(
    Bundle(
        "project_summary",
        Project.project_id,
        Project.project_name,
        Project.is_default,
        Project.created_at,
        Project.updated_at,
    )
)


async def list_project_summaries(
    db: AsyncSession, user_id: UUID, params: ListParams
):
    stmt = PROJECT_SUMMARIES.where(
        Project.user_id == user_id, Project.delete_yn == DeleteYN.N
    )
    rows, page = await fetch_page(
        db, stmt, model=Project, id_name="project_id", params=params
    )
    return {
        "items": [ProjectSummary.model_validate(row) for row in rows],
        "page": page,
    }
