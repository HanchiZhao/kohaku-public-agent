from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from kohakuterrarium.studio.persistence.session_index import (
    close_session_index,
    get_session_index_default,
    sidecar_path_for,
)

from app.agent_service import AgentService


class KohakuIndexShutdownTests(
    IsolatedAsyncioTestCase
):
    """Verify AgentService releases the global session-index file."""

    async def test_service_close_releases_session_index_file(
        self,
    ) -> None:
        root = Path(
            tempfile.mkdtemp(
                prefix="kohaku-index-shutdown-test-"
            )
        ).resolve()

        session_root = (
            root
            / "runtime"
            / "kohaku_sessions"
        )

        service: AgentService | None = None

        try:
            service = AgentService(
                project_root=root,
                workspace_root=(
                    root
                    / "runtime"
                    / "workspaces"
                ),
                session_root=session_root,
                database_path=(
                    root
                    / "runtime"
                    / "data"
                    / "agent.db"
                ),
            )

            await service.start()

            index = get_session_index_default(
                session_root
            )

            index.meta_put(
                "index_shutdown_test",
                "active",
            )

            sidecar_path = sidecar_path_for(
                session_root
            )

            self.assertTrue(
                sidecar_path.exists()
            )

            await service.close()
            service = None

            # Regression assertion for WinError 32. If the global
            # sidecar handle is still open, Windows raises here.
            shutil.rmtree(root)

            self.assertFalse(
                root.exists()
            )

        finally:
            if service is not None:
                await service.close()

            close_session_index()

            if root.exists():
                shutil.rmtree(
                    root,
                    ignore_errors=True,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()