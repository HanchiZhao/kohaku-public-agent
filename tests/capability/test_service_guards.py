from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from app.agent_service import (
    AgentService,
    ServiceNotStartedError,
)


class AgentServiceGuardTests(
    IsolatedAsyncioTestCase
):
    """Verify invalid lifecycle usage is rejected safely."""

    async def asyncSetUp(self) -> None:
        self.temporary_directory = (
            TemporaryDirectory(
                prefix=(
                    "kohaku-agent-"
                    "service-guard-"
                )
            )
        )

        self.root = Path(
            self.temporary_directory.name
        )

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_service(self) -> AgentService:
        return AgentService(
            project_root=self.root,
            workspace_root=(
                self.root / "workspaces"
            ),
            database_path=(
                self.root / "agent.db"
            ),
        )

    async def test_service_is_initially_stopped(
        self,
    ) -> None:
        service = self.create_service()

        self.assertFalse(
            service.is_started
        )

    async def test_list_sessions_before_start_raises(
        self,
    ) -> None:
        service = self.create_service()

        with self.assertRaises(
            ServiceNotStartedError
        ):
            await service.list_sessions()

    async def test_close_before_start_is_safe(
        self,
    ) -> None:
        service = self.create_service()

        await service.close()

        self.assertFalse(
            service.is_started
        )

    async def test_start_is_idempotent(
        self,
    ) -> None:
        service = self.create_service()

        try:
            await service.start()

            first_studio = service._studio

            await service.start()

            second_studio = service._studio

            self.assertTrue(
                service.is_started
            )

            self.assertIs(
                first_studio,
                second_studio,
            )

        finally:
            await service.close()

        self.assertFalse(
            service.is_started
        )