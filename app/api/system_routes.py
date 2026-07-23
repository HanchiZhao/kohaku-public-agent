from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from app.agent_service import AgentService
from app.api.dependencies import get_agent_service
from app.api.schemas import SystemStatusResponse
from app.persistence import (
    ACTIVE_STATUS,
    DELETED_STATUS,
    RECOVERY_FAILED_STATUS,
)


router = APIRouter(
    prefix="/api/v1/system",
    tags=["system"],
)


async def database_is_reachable(
    service: Any,
) -> bool:
    """Return whether the application database answers a basic query."""

    if service is None:
        return False

    database = getattr(
        service,
        "database",
        None,
    )

    if database is None:
        # Compatibility with lightweight HTTP test doubles that do not
        # implement the real persistence layer.
        return bool(
            getattr(
                service,
                "is_started",
                False,
            )
        )

    ping = getattr(
        database,
        "ping",
        None,
    )

    if not callable(ping):
        return False

    try:
        return bool(
            await ping()
        )
    except Exception:
        return False


def _candidate_session_paths(
    *,
    session_root: Path,
    stored_value: str | None,
    creature_id: str | None,
    studio_session_id: str | None,
) -> list[Path]:
    """Build possible session-file paths for current and legacy rows."""

    values: list[str] = []

    for value in (
        stored_value,
        creature_id,
        studio_session_id,
    ):
        if value is None:
            continue

        normalized = str(value).strip()

        if (
            normalized
            and normalized not in values
        ):
            values.append(normalized)

    candidates: list[Path] = []

    for value in values:
        supplied = Path(
            value
        ).expanduser()

        if supplied.is_absolute():
            path_candidates = [
                supplied,
            ]
        else:
            path_candidates = [
                session_root / supplied,
            ]

        if supplied.suffix != ".kohakutr":
            path_candidates.append(
                session_root
                / f"{supplied.name}.kohakutr"
            )

        for candidate in path_candidates:
            resolved = candidate.resolve()

            if resolved not in candidates:
                candidates.append(
                    resolved
                )

    return candidates


def _resolve_existing_session_path(
    *,
    session_root: Path,
    stored_value: str | None,
    creature_id: str | None,
    studio_session_id: str | None,
) -> Path | None:
    """Return the first existing session file for one database row."""

    for candidate in _candidate_session_paths(
        session_root=session_root,
        stored_value=stored_value,
        creature_id=creature_id,
        studio_session_id=studio_session_id,
    ):
        if candidate.is_file():
            return candidate

    return None


async def build_system_status(
    service: AgentService,
) -> SystemStatusResponse:
    """Collect non-sensitive runtime and persistence statistics."""

    started = bool(
        getattr(
            service,
            "is_started",
            False,
        )
    )

    database_reachable = (
        await database_is_reachable(
            service
        )
    )

    live_sessions: list[Any] = []

    if started:
        try:
            live_sessions = (
                await service.list_sessions()
            )
        except Exception:
            live_sessions = []

    busy_sessions = sum(
        1
        for session in live_sessions
        if bool(
            getattr(
                session,
                "is_busy",
                False,
            )
        )
    )

    conversations: list[Any] = []

    repository = getattr(
        service,
        "repository",
        None,
    )

    owner_external_key = getattr(
        service,
        "owner_external_key",
        "development-user",
    )

    if (
        database_reachable
        and repository is not None
    ):
        try:
            conversations = (
                await repository.list_conversations(
                    owner_external_key=(
                        owner_external_key
                    ),
                    include_deleted=True,
                )
            )
        except Exception:
            database_reachable = False
            conversations = []

    active_conversations = sum(
        1
        for conversation in conversations
        if conversation.status == ACTIVE_STATUS
    )

    recovery_failed_conversations = sum(
        1
        for conversation in conversations
        if (
            conversation.status
            == RECOVERY_FAILED_STATUS
        )
    )

    deleted_conversations = sum(
        1
        for conversation in conversations
        if conversation.status == DELETED_STATUS
    )

    session_root = Path(
        getattr(
            service,
            "session_root",
            Path("runtime")
            / "kohaku_sessions",
        )
    ).expanduser().resolve()

    referenced_paths: set[Path] = set()
    missing_session_files = 0

    for conversation in conversations:
        resolved_path = (
            _resolve_existing_session_path(
                session_root=session_root,
                stored_value=(
                    conversation
                    .studio_session_name
                ),
                creature_id=(
                    conversation.creature_id
                ),
                studio_session_id=(
                    conversation
                    .studio_session_id
                ),
            )
        )

        candidates = _candidate_session_paths(
            session_root=session_root,
            stored_value=(
                conversation
                .studio_session_name
            ),
            creature_id=(
                conversation.creature_id
            ),
            studio_session_id=(
                conversation
                .studio_session_id
            ),
        )

        if resolved_path is not None:
            referenced_paths.add(
                resolved_path.resolve()
            )
        else:
            referenced_paths.update(
                candidates
            )

            if (
                conversation.status
                != DELETED_STATUS
            ):
                missing_session_files += 1

    actual_session_files: set[Path] = set()

    if session_root.is_dir():
        actual_session_files = {
            path.resolve()
            for path in session_root.glob(
                "*.kohakutr"
            )
            if path.is_file()
        }

    orphan_session_files = len(
        actual_session_files
        - referenced_paths
    )

    healthy = (
        started
        and database_reachable
        and recovery_failed_conversations == 0
        and missing_session_files == 0
    )

    return SystemStatusResponse(
        status=(
            "ok"
            if healthy
            else "degraded"
        ),
        agent_service_started=started,
        database_reachable=(
            database_reachable
        ),
        live_sessions=len(
            live_sessions
        ),
        busy_sessions=busy_sessions,
        active_conversations=(
            active_conversations
        ),
        recovery_failed_conversations=(
            recovery_failed_conversations
        ),
        deleted_conversations=(
            deleted_conversations
        ),
        missing_session_files=(
            missing_session_files
        ),
        orphan_session_files=(
            orphan_session_files
        ),
    )


@router.get(
    "/status",
    response_model=SystemStatusResponse,
    summary="Inspect runtime and persistence status",
)
async def get_system_status(
    service: AgentService = Depends(
        get_agent_service
    ),
) -> SystemStatusResponse:
    """Return non-sensitive system consistency statistics."""

    return await build_system_status(
        service
    )