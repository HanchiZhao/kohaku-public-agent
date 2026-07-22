from __future__ import annotations

from fastapi import Request

from app.agent_service import (
    AgentService,
    ServiceNotStartedError,
)


def get_agent_service(request: Request) -> AgentService:
    """Return the long-running AgentService owned by FastAPI."""

    service = getattr(
        request.app.state,
        "agent_service",
        None,
    )

    if service is None:
        raise ServiceNotStartedError(
            "The Agent service is not available."
        )

    return service