from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.integrations import router as integrations_router
from backend.app.api.routes.projects import router as projects_router
from backend.app.api.routes.agent import router as agent_router

__all__ = [
    "health_router",
    "integrations_router",
    "projects_router",
    "agent_router",
]

