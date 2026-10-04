from fastapi import APIRouter

from api_service.api.v1.routes import (
    messages,
    projects,
    runs,
    sessions,
    run_diagnostics,
    users,
    workflows,
)

# Lifecycle is owned by service_bootstrap, never by router registration.
api_router = APIRouter()
api_router.include_router(users.router)
api_router.include_router(projects.router)
api_router.include_router(sessions.router)
api_router.include_router(messages.router)
api_router.include_router(runs.router)
api_router.include_router(workflows.router)
api_router.include_router(run_diagnostics.router)
api_router.include_router(run_diagnostics.admin_router)
