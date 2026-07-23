from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SessionInfo
from app.persistence import (
    ACTIVE_STATUS,
    DELETED_STATUS,
    RECOVERY_FAILED_STATUS,
)


@dataclass(slots=True)
class _FakeConversation:
    public_id: str
    title: str
    status: str

    studio_session_name: str | None
    studio_session_id: str | None
    creature_id: str | None

    recovery_error: str | None = None


class _FakeDatabase:
    def __init__(
        self,
    ) -> None:
        self.is_started = False

    async def start(self) -> None:
        self.is_started = True

    async def close(self) -> None:
        self.is_started = False

    async def ping(self) -> bool:
        return self.is_started


class _FakeRepository:
    def __init__(
        self,
        records: list[_FakeConversation],
    ) -> None:
        self.records = records

    async def list_conversations(
        self,
        *,
        owner_external_key: str,
        include_deleted: bool = False,
    ) -> list[_FakeConversation]:
        del owner_external_key

        if include_deleted:
            return list(
                self.records
            )

        return [
            record
            for record in self.records
            if (
                record.status
                != DELETED_STATUS
            )
        ]


class _FakeAgentService:
    def __init__(
        self,
        *,
        session_root: Path,
        records: list[_FakeConversation],
        sessions: list[SessionInfo],
    ) -> None:
        self._started = False

        self.database = (
            _FakeDatabase()
        )

        self.repository = (
            _FakeRepository(
                records
            )
        )

        self.owner_external_key = (
            "development-user"
        )

        self.session_root = (
            session_root
        )

        self.sessions = sessions

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        await self.database.start()
        self._started = True

    async def close(self) -> None:
        self._started = False
        await self.database.close()

    async def list_sessions(
        self,
    ) -> list[SessionInfo]:
        return list(
            self.sessions
        )


class SystemStatusTests(TestCase):
    """Verify health and persistence status endpoints."""

    def test_clean_system_status(
        self,
    ) -> None:
        with TemporaryDirectory(
            prefix=(
                "kohaku-system-status-clean-"
            )
        ) as temporary_directory:
            root = Path(
                temporary_directory
            )

            session_root = (
                root / "sessions"
            )
            session_root.mkdir()

            session_file = (
                session_root
                / "creature-clean.kohakutr"
            )
            session_file.touch()

            records = [
                _FakeConversation(
                    public_id="public-clean",
                    title="Clean conversation",
                    status=ACTIVE_STATUS,
                    studio_session_name=str(
                        session_file
                    ),
                    studio_session_id="graph-clean",
                    creature_id="creature-clean",
                ),
            ]

            sessions = [
                SessionInfo(
                    public_id="public-clean",
                    studio_session_id=(
                        "graph-clean"
                    ),
                    creature_id=(
                        "creature-clean"
                    ),
                    name=(
                        "Clean conversation"
                    ),
                    workspace=(
                        root / "workspace"
                    ),
                    created_at=datetime.now(
                        timezone.utc
                    ),
                    is_busy=False,
                ),
            ]

            service = _FakeAgentService(
                session_root=session_root,
                records=records,
                sessions=sessions,
            )

            application = create_app(
                service_factory=(
                    lambda: service
                )  # type: ignore[arg-type]
            )

            with TestClient(
                application
            ) as client:
                health = client.get(
                    "/health"
                )

                self.assertEqual(
                    health.status_code,
                    200,
                )

                self.assertEqual(
                    health.json(),
                    {
                        "status": "ok",
                        "agent_service_started": True,
                        "database_reachable": True,
                    },
                )

                status_response = client.get(
                    "/api/v1/system/status"
                )

                self.assertEqual(
                    status_response.status_code,
                    200,
                )

                payload = (
                    status_response.json()
                )

                self.assertEqual(
                    payload["status"],
                    "ok",
                )

                self.assertEqual(
                    payload[
                        "active_conversations"
                    ],
                    1,
                )

                self.assertEqual(
                    payload[
                        "missing_session_files"
                    ],
                    0,
                )

                self.assertEqual(
                    payload[
                        "orphan_session_files"
                    ],
                    0,
                )

    def test_degraded_status_reports_counts(
        self,
    ) -> None:
        with TemporaryDirectory(
            prefix=(
                "kohaku-system-status-"
                "degraded-"
            )
        ) as temporary_directory:
            root = Path(
                temporary_directory
            )

            session_root = (
                root / "sessions"
            )
            session_root.mkdir()

            existing_file = (
                session_root
                / "creature-existing.kohakutr"
            )
            existing_file.touch()

            deleted_file = (
                session_root
                / "creature-deleted.kohakutr"
            )
            deleted_file.touch()

            orphan_file = (
                session_root
                / "orphan.kohakutr"
            )
            orphan_file.touch()

            records = [
                _FakeConversation(
                    public_id="public-existing",
                    title="Existing",
                    status=ACTIVE_STATUS,
                    studio_session_name=str(
                        existing_file
                    ),
                    studio_session_id=(
                        "graph-existing"
                    ),
                    creature_id=(
                        "creature-existing"
                    ),
                ),
                _FakeConversation(
                    public_id="public-missing",
                    title="Missing",
                    status=ACTIVE_STATUS,
                    studio_session_name=str(
                        session_root
                        / "missing.kohakutr"
                    ),
                    studio_session_id=(
                        "graph-missing"
                    ),
                    creature_id=(
                        "creature-missing"
                    ),
                ),
                _FakeConversation(
                    public_id="public-failed",
                    title="Failed",
                    status=(
                        RECOVERY_FAILED_STATUS
                    ),
                    studio_session_name=str(
                        session_root
                        / "failed.kohakutr"
                    ),
                    studio_session_id=(
                        "graph-failed"
                    ),
                    creature_id=(
                        "creature-failed"
                    ),
                    recovery_error=(
                        "Session file not found."
                    ),
                ),
                _FakeConversation(
                    public_id="public-deleted",
                    title="Deleted",
                    status=DELETED_STATUS,
                    studio_session_name=str(
                        deleted_file
                    ),
                    studio_session_id=(
                        "graph-deleted"
                    ),
                    creature_id=(
                        "creature-deleted"
                    ),
                ),
            ]

            sessions = [
                SessionInfo(
                    public_id="public-existing",
                    studio_session_id=(
                        "graph-existing"
                    ),
                    creature_id=(
                        "creature-existing"
                    ),
                    name="Existing",
                    workspace=(
                        root / "workspace"
                    ),
                    created_at=datetime.now(
                        timezone.utc
                    ),
                    is_busy=True,
                ),
            ]

            service = _FakeAgentService(
                session_root=session_root,
                records=records,
                sessions=sessions,
            )

            application = create_app(
                service_factory=(
                    lambda: service
                )  # type: ignore[arg-type]
            )

            with TestClient(
                application
            ) as client:
                response = client.get(
                    "/api/v1/system/status"
                )

                self.assertEqual(
                    response.status_code,
                    200,
                )

                payload = response.json()

                self.assertEqual(
                    payload["status"],
                    "degraded",
                )

                self.assertEqual(
                    payload["live_sessions"],
                    1,
                )

                self.assertEqual(
                    payload["busy_sessions"],
                    1,
                )

                self.assertEqual(
                    payload[
                        "active_conversations"
                    ],
                    2,
                )

                self.assertEqual(
                    payload[
                        "recovery_failed_conversations"
                    ],
                    1,
                )

                self.assertEqual(
                    payload[
                        "deleted_conversations"
                    ],
                    1,
                )

                self.assertEqual(
                    payload[
                        "missing_session_files"
                    ],
                    2,
                )

                self.assertEqual(
                    payload[
                        "orphan_session_files"
                    ],
                    1,
                )