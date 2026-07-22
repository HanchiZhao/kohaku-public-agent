from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.agent_service import (
    AgentService,
    AgentServiceError,
    InvalidMessageError,
    PublicSessionNotFoundError,
    ServiceClosingError,
    ServiceNotStartedError,
)
from app.api.routes import router
from app.api.schemas import HealthResponse


ServiceFactory = Callable[[], AgentService]


def create_app(
    service_factory: ServiceFactory = AgentService,
) -> FastAPI:
    """Create the FastAPI application.

    A factory function makes the application easier to test because
    automated tests can provide a fake AgentService.
    """

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        service = service_factory()

        await service.start()

        application.state.agent_service = service

        try:
            yield
        finally:
            await service.close()

    application = FastAPI(
        title="Kohaku Public Agent API",
        description=(
            "Development API for the public AI Agent platform."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    application.include_router(router)

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check API and AgentService health",
    )
    async def health(
        request: Request,
    ) -> HealthResponse:
        service = getattr(
            request.app.state,
            "agent_service",
            None,
        )

        started = bool(
            service is not None
            and service.is_started
        )

        return HealthResponse(
            status="ok" if started else "degraded",
            agent_service_started=started,
        )

    @application.exception_handler(
        PublicSessionNotFoundError
    )
    async def session_not_found_handler(
        request: Request,
        exception: PublicSessionNotFoundError,
    ) -> JSONResponse:
        del request

        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exception)},
        )

    @application.exception_handler(
        InvalidMessageError
    )
    async def invalid_message_handler(
        request: Request,
        exception: InvalidMessageError,
    ) -> JSONResponse:
        del request

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exception)},
        )

    @application.exception_handler(
        ServiceNotStartedError
    )
    @application.exception_handler(
        ServiceClosingError
    )
    async def service_unavailable_handler(
        request: Request,
        exception: AgentServiceError,
    ) -> JSONResponse:
        del request

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exception)},
        )

    @application.exception_handler(
        AgentServiceError
    )
    async def agent_service_error_handler(
        request: Request,
        exception: AgentServiceError,
    ) -> JSONResponse:
        del request

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exception)},
        )

    return application


app = create_app()