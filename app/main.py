from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Callable,
)
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles

from app.agent_service import (
    AgentService,
    AgentServiceError,
    InvalidMessageError,
    PublicSessionNotFoundError,
    ServiceClosingError,
    ServiceNotStartedError,
    SessionBusyError,
)
from app.api.routes import router
from app.api.schemas import HealthResponse
from app.api.system_routes import (
    database_is_reachable,
    router as system_router,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

FRONTEND_DIR = (
    PROJECT_ROOT / "frontend"
)

INDEX_FILE = (
    FRONTEND_DIR / "index.html"
)

ServiceFactory = Callable[
    [],
    AgentService,
]


def create_app(
    service_factory: ServiceFactory = AgentService,
) -> FastAPI:
    """Create the FastAPI application."""

    if not FRONTEND_DIR.is_dir():
        raise RuntimeError(
            "Frontend directory does not exist: "
            f"{FRONTEND_DIR}"
        )

    if not INDEX_FILE.is_file():
        raise RuntimeError(
            "Frontend index file does not exist: "
            f"{INDEX_FILE}"
        )

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        service = service_factory()

        await service.start()

        application.state.agent_service = (
            service
        )

        try:
            yield
        finally:
            await service.close()

    application = FastAPI(
        title="Kohaku Public Agent API",
        description=(
            "Development API for the public "
            "AI Agent platform."
        ),
        version="0.5.0",
        lifespan=lifespan,
    )

    application.mount(
        "/static",
        StaticFiles(
            directory=str(
                FRONTEND_DIR
            )
        ),
        name="static",
    )

    application.include_router(
        router
    )

    application.include_router(
        system_router
    )

    @application.get(
        "/",
        include_in_schema=False,
    )
    async def frontend_index() -> FileResponse:
        return FileResponse(
            INDEX_FILE,
            media_type="text/html",
        )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check API, AgentService and database health",
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

        database_reachable = (
            await database_is_reachable(
                service
            )
        )

        healthy = (
            started
            and database_reachable
        )

        return HealthResponse(
            status=(
                "ok"
                if healthy
                else "degraded"
            ),
            agent_service_started=(
                started
            ),
            database_reachable=(
                database_reachable
            ),
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
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            content={
                "detail": str(exception),
            },
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
            status_code=(
                status
                .HTTP_422_UNPROCESSABLE_ENTITY
            ),
            content={
                "detail": str(exception),
            },
        )

    @application.exception_handler(
        SessionBusyError
    )
    async def session_busy_handler(
        request: Request,
        exception: SessionBusyError,
    ) -> JSONResponse:
        del request

        return JSONResponse(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            content={
                "detail": str(exception),
            },
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
            status_code=(
                status
                .HTTP_503_SERVICE_UNAVAILABLE
            ),
            content={
                "detail": str(exception),
            },
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
            status_code=(
                status
                .HTTP_500_INTERNAL_SERVER_ERROR
            ),
            content={
                "detail": str(exception),
            },
        )

    return application


app = create_app()