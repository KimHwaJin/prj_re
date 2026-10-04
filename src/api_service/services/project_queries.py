"""Owner-scoped project summary page without ORM or prompt hydration."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Bundle

from api_service.core.enums import DeleteYN
from api_service.core.pagination import ListParams, fetch_page
from api_service.models.common.project_model import ProjectModel as Project
from api_service.schemas.common.project_schema import ProjectSummary


PROJECT_SUMMARIES = select(Bundle("project_summary",
    Project.project_id, Project.project_name, Project.is_default,
    Project.created_at, Project.updated_at,
))


async def list_project_summaries(db: AsyncSession, user_id: UUID, params: ListParams):
    stmt = PROJECT_SUMMARIES.where(Project.user_id == user_id, Project.delete_yn == DeleteYN.N)
    rows, page = await fetch_page(db, stmt, model=Project, id_name="project_id", params=params)
    return {"items": [ProjectSummary.model_validate(row) for row in rows], "page": page}
