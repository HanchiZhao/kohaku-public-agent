from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from app.agent_service import (
    AgentService,
    ServiceNotStartedError,
)


class AgentServiceGuardTests(IsolatedAsyncioTestCase):
    """Verify that invalid lifecycle usage is rejected safely."""

    async def test_service_is_initially_stopped(self) -> None:
        service = AgentService()

        self.assertFalse(service.is_started)

    async def test_list_sessions_before_start_raises(self) -> None:
        service = AgentService()

        with self.assertRaises(ServiceNotStartedError):
            await service.list_sessions()

    async def test_close_before_start_is_safe(self) -> None:
        service = AgentService()

        await service.close()

        self.assertFalse(service.is_started)

    async def test_start_is_idempotent(self) -> None:
        service = AgentService()

        try:
            await service.start()
            first_studio = service._studio

            await service.start()
            second_studio = service._studio

            self.assertTrue(service.is_started)
            self.assertIs(first_studio, second_studio)

        finally:
            await service.close()

        self.assertFalse(service.is_started)