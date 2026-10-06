"""Resolve authorized project instructions once per user turn or legacy backfill."""
from uuid import UUID
from dtest.contracts.session_settings import KERNEL_PROFILE
from dtest.infrastructure.database.runtime import short_session
from sqlalchemy import select
from dtest.infrastructure.database.models.project_model import ProjectModel
from dtest.infrastructure.database.models.session_model import SessionModel


async def load_project_snapshot(db, *, user_id, session_id, project_id=None):
    query = select(ProjectModel,SessionModel.settings).join(SessionModel, SessionModel.project_id == ProjectModel.project_id).where(
        SessionModel.session_id == UUID(str(session_id)), SessionModel.user_id == UUID(str(user_id)))
    if project_id is not None:
        query = query.where(ProjectModel.project_id == UUID(str(project_id)))
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise ValueError("Project context does not belong to this user/session")
    project,settings = row
    result={"project_system_prompt": project.system_prompt or "", "project_prompt_version": project.prompt_version}
    profile=(settings or {}).get('kernel_profile')
    if profile is not None:
        # Validate stored syntax defensively; changing the creation allowlist
        # must not replace or invalidate a session's already-pinned selection.
        result['kernel_profile']=KERNEL_PROFILE.validate_python(profile)
    return result


async def read_project_snapshot(*, user_id, session_id, project_id=None, session_factory=None):
    """Return plain values and release the service connection before graph I/O."""
    async with short_session(session_factory) as db:
        return await load_project_snapshot(
            db, user_id=user_id, session_id=session_id, project_id=project_id,
        )


async def ensure_project_snapshot(graph, config, *, user_id, session_id, session_factory=None, snapshot=None):
    if snapshot is None:
        snapshot = await graph.aget_state(config)
    if snapshot.values and "project_system_prompt" not in snapshot.values:
        update = await read_project_snapshot(
            user_id=user_id, session_id=session_id,
            project_id=snapshot.values.get("project_id"), session_factory=session_factory,
        )
        await graph.aupdate_state(config, update)


async def load_event_project_snapshot(values):
    return await read_project_snapshot(
        user_id=values["user_id"], session_id=values["session_id"],
        project_id=values["project_id"],
    )
