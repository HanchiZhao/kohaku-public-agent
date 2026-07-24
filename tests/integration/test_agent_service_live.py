from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import (
    IsolatedAsyncioTestCase,
    skipUnless,
)
from uuid import uuid4

from app.agent_service import (
    AgentService,
    PublicSessionNotFoundError,
)


RUN_LIVE_TESTS = (
    os.getenv("RUN_LIVE_AGENT_TESTS")
    == "1"
)


@skipUnless(
    RUN_LIVE_TESTS,
    (
        "Set RUN_LIVE_AGENT_TESTS=1 "
        "to run the live model test."
    ),
)
class AgentServiceLiveTests(
    IsolatedAsyncioTestCase
):
    """Integration tests that call the configured model."""

    async def asyncSetUp(self) -> None:
        self.temp_directory = (
            TemporaryDirectory(
                prefix="kohaku-agent-live-"
            )
        )

        root = Path(
            self.temp_directory.name
        )

        self.service = AgentService(
            project_root=root,
            workspace_root=(
                root / "workspaces"
            ),
            database_path=(
                root / "agent.db"
            ),
        )

        await self.service.start()

        self.session = (
            await self.service.create_session(
                name="automated-live-test"
            )
        )

    async def asyncTearDown(self) -> None:
        try:
            await self.service.delete_session(
                self.session.public_id,
                delete_workspace=True,
            )
        except PublicSessionNotFoundError:
            pass
        finally:
            await self.service.close()
            self.temp_directory.cleanup()

    async def test_multiturn_memory_and_history(
        self,
    ) -> None:
        test_code = (
            "KT-LIVE-"
            + uuid4().hex[:8].upper()
        )

        first_response = (
            await self.service.chat(
                self.session.public_id,
                (
                    f"请记住测试代号 {test_code}。"
                    "这是自动化多轮会话测试。"
                    "请只回复：已记住"
                ),
            )
        )

        self.assertTrue(
            first_response.strip()
        )

        second_response = (
            await self.service.chat(
                self.session.public_id,
                (
                    "请只回复我上一条消息中的"
                    "测试代号，不要添加任何解释。"
                ),
            )
        )

        self.assertIn(
            test_code,
            second_response,
        )

        history = await self.service.history(
            self.session.public_id
        )

        self.assertTrue(history)
        self.assertIn(
            "messages",
            history,
        )

        session_info = (
            await self.service.get_session(
                self.session.public_id
            )
        )

        self.assertFalse(
            session_info.is_busy
        )

    async def test_session_can_be_deleted(
        self,
    ) -> None:
        public_id = self.session.public_id

        await self.service.delete_session(
            public_id,
            delete_workspace=True,
        )

        with self.assertRaises(
            PublicSessionNotFoundError
        ):
            await self.service.get_session(
                public_id
            )