from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import IsolatedAsyncioTestCase

from app.agent_service import AgentService


@dataclass(slots=True)
class _FakeSession:
    session_id: str
    name: str
    creatures: list[dict[str, str]]


class _FakeSavedStore:
    """Shared persisted state across fake Studio instances."""

    def __init__(self) -> None:
        self.saved: dict[
            str,
            dict[str, str],
        ] = {}

        self.counter = 0

    def next_id(
        self,
        prefix: str,
    ) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"


class _FakeChatNamespace:
    def chat(
        self,
        session_id: str,
        creature_id: str,
        content: str,
    ) -> AsyncIterator[str]:
        del session_id
        del creature_id

        async def stream() -> AsyncIterator[str]:
            yield f"Echo: {content}"

        return stream()

    def history(
        self,
        session_id: str,
        creature_id: str,
    ) -> dict[str, Any]:
        del session_id
        del creature_id

        return {
            "messages": [],
        }


class _FakeControlNamespace:
    async def interrupt(
        self,
        session_id: str,
        creature_id: str,
    ) -> None:
        del session_id
        del creature_id


class _FakeSessionsNamespace:
    def __init__(
        self,
        studio: _FakeStudio,
    ) -> None:
        self.studio = studio

        self.chat = _FakeChatNamespace()
        self.ctl = _FakeControlNamespace()

    async def start_creature(
        self,
        creature_ref: str,
        *,
        pwd: str,
        llm: str | None,
        name: str,
    ) -> _FakeSession:
        del creature_ref
        del pwd
        del llm

        return self.studio.create_new_session(
            display_name=name
        )

    async def stop(
        self,
        session_id: str,
    ) -> None:
        self.studio.active.pop(
            session_id,
            None,
        )


class _FakePersistenceNamespace:
    def __init__(
        self,
        studio: _FakeStudio,
    ) -> None:
        self.studio = studio

    def resolve_path(
        self,
        name: str,
    ) -> Path | None:
        supplied = Path(name)

        if supplied.is_absolute():
            candidate = supplied
        else:
            candidate = (
                self.studio.session_root
                / supplied
            )

        if candidate.exists():
            return candidate

        if candidate.suffix != ".kohakutr":
            candidate = candidate.with_name(
                f"{candidate.name}.kohakutr"
            )

        if candidate.exists():
            return candidate

        return None

    async def resume(
        self,
        path: str | Path,
        *,
        pwd_override: str | None = None,
        llm: str | None = None,
    ) -> _FakeSession:
        del pwd_override
        del llm

        resolved = Path(path).resolve()

        if not resolved.exists():
            raise RuntimeError(
                "Saved session file does not exist: "
                f"{resolved}"
            )

        saved = self.studio.store.saved.get(
            str(resolved)
        )

        if saved is None:
            raise RuntimeError(
                "Saved session metadata does not exist: "
                f"{resolved}"
            )

        live_session_id = (
            self.studio.store.next_id(
                "graph"
            )
        )

        session = _FakeSession(
            session_id=live_session_id,
            name=saved["display_name"],
            creatures=[
                {
                    "creature_id": (
                        saved["creature_id"]
                    ),
                }
            ],
        )

        self.studio.active[
            live_session_id
        ] = session

        return session

    def delete(
        self,
        name: str,
    ) -> list[Path]:
        path = self.resolve_path(name)

        if path is None:
            return []

        resolved = path.resolve()

        self.studio.store.saved.pop(
            str(resolved),
            None,
        )

        path.unlink(
            missing_ok=True
        )

        return [resolved]


class _FakeStudio:
    def __init__(
        self,
        store: _FakeSavedStore,
    ) -> None:
        self.store = store

        self.session_root = Path(
            os.environ["KT_SESSION_DIR"]
        ).resolve()

        self.session_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.active: dict[
            str,
            _FakeSession,
        ] = {}

        self.sessions = (
            _FakeSessionsNamespace(self)
        )

        self.persistence = (
            _FakePersistenceNamespace(self)
        )

    async def __aenter__(
        self,
    ) -> _FakeStudio:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        del exc_type
        del exc
        del traceback

        await self.shutdown()

    async def shutdown(self) -> None:
        self.active.clear()

    def create_new_session(
        self,
        *,
        display_name: str,
    ) -> _FakeSession:
        session_id = (
            self.store.next_id(
                "graph"
            )
        )

        creature_id = (
            self.store.next_id(
                "creature"
            )
        )

        # Match the installed KohakuTerrarium Studio behavior:
        # a standalone Creature is stored as
        # <KT_SESSION_DIR>/<creature_id>.kohakutr.
        session_path = (
            self.session_root
            / f"{creature_id}.kohakutr"
        )

        session_path.touch()

        self.store.saved[
            str(session_path.resolve())
        ] = {
            "creature_id": creature_id,
            "display_name": display_name,
        }

        session = _FakeSession(
            session_id=session_id,
            name=display_name,
            creatures=[
                {
                    "creature_id": creature_id,
                }
            ],
        )

        self.active[
            session_id
        ] = session

        return session


class RestartRecoveryTests(
    IsolatedAsyncioTestCase
):
    """Verify SQLite-to-SessionStore restart recovery."""

    async def test_restart_restores_and_delete_prevents_return(
        self,
    ) -> None:
        with TemporaryDirectory(
            prefix=(
                "kohaku-public-agent-"
                "restart-test-"
            )
        ) as temporary_directory:
            root = Path(
                temporary_directory
            )

            database_path = (
                root
                / "runtime"
                / "data"
                / "agent.db"
            )

            workspace_root = (
                root
                / "runtime"
                / "workspaces"
            )

            session_root = (
                root
                / "runtime"
                / "kohaku_sessions"
            )

            store = _FakeSavedStore()

            def studio_factory() -> _FakeStudio:
                return _FakeStudio(store)

            first_service = AgentService(
                project_root=root,
                workspace_root=workspace_root,
                session_root=session_root,
                database_path=database_path,
                studio_factory=studio_factory,
            )

            await first_service.start()

            try:
                created = (
                    await first_service
                    .create_session(
                        name=(
                            "Restart recovery test"
                        )
                    )
                )

                public_id = created.public_id

                persisted = (
                    await first_service
                    .repository
                    .get_conversation(
                        public_id=public_id,
                        owner_external_key=(
                            "development-user"
                        ),
                    )
                )

                self.assertIsNotNone(
                    persisted.studio_session_name
                )

                saved_path = Path(
                    persisted.studio_session_name
                    or ""
                )

                self.assertTrue(
                    saved_path.is_absolute()
                )

                self.assertTrue(
                    saved_path.exists()
                )

                self.assertEqual(
                    saved_path.parent,
                    session_root.resolve(),
                )

                self.assertEqual(
                    saved_path.name,
                    (
                        f"{created.creature_id}"
                        ".kohakutr"
                    ),
                )

            finally:
                await first_service.close()

            # Closing the service must not delete the session file.
            self.assertTrue(
                saved_path.exists()
            )

            second_service = AgentService(
                project_root=root,
                workspace_root=workspace_root,
                session_root=session_root,
                database_path=database_path,
                studio_factory=studio_factory,
            )

            await second_service.start()

            try:
                restored_sessions = (
                    await second_service
                    .list_sessions()
                )

                self.assertEqual(
                    len(restored_sessions),
                    1,
                )

                restored = (
                    restored_sessions[0]
                )

                self.assertEqual(
                    restored.public_id,
                    public_id,
                )

                self.assertEqual(
                    restored.name,
                    "Restart recovery test",
                )

                self.assertTrue(
                    restored.studio_session_id
                )

                self.assertEqual(
                    restored.creature_id,
                    created.creature_id,
                )

                await (
                    second_service
                    .delete_session(
                        public_id,
                        delete_workspace=True,
                    )
                )

            finally:
                await second_service.close()

            self.assertFalse(
                saved_path.exists()
            )

            third_service = AgentService(
                project_root=root,
                workspace_root=workspace_root,
                session_root=session_root,
                database_path=database_path,
                studio_factory=studio_factory,
            )

            await third_service.start()

            try:
                self.assertEqual(
                    await third_service
                    .list_sessions(),
                    [],
                )

            finally:
                await third_service.close()

    async def test_legacy_runtime_id_mapping_is_repaired(
        self,
    ) -> None:
        """Old graph-ID mappings should fall back to Creature IDs."""

        with TemporaryDirectory(
            prefix=(
                "kohaku-public-agent-"
                "legacy-mapping-test-"
            )
        ) as temporary_directory:
            root = Path(
                temporary_directory
            )

            database_path = (
                root / "agent.db"
            )

            workspace_root = (
                root / "workspaces"
            )

            session_root = (
                root / "kohaku_sessions"
            )

            store = _FakeSavedStore()

            def studio_factory() -> _FakeStudio:
                return _FakeStudio(store)

            first_service = AgentService(
                project_root=root,
                workspace_root=workspace_root,
                session_root=session_root,
                database_path=database_path,
                studio_factory=studio_factory,
            )

            await first_service.start()

            try:
                created = (
                    await first_service
                    .create_session(
                        name="Legacy mapping test"
                    )
                )

                # Simulate the previous incorrect implementation,
                # which stored the graph/runtime ID instead of
                # the actual .kohakutr path.
                await (
                    first_service.repository
                    .update_runtime_binding(
                        public_id=created.public_id,
                        owner_external_key=(
                            "development-user"
                        ),
                        studio_session_name=(
                            created
                            .studio_session_id
                        ),
                        studio_session_id=(
                            created
                            .studio_session_id
                        ),
                        creature_id=(
                            created.creature_id
                        ),
                    )
                )

            finally:
                await first_service.close()

            second_service = AgentService(
                project_root=root,
                workspace_root=workspace_root,
                session_root=session_root,
                database_path=database_path,
                studio_factory=studio_factory,
            )

            await second_service.start()

            try:
                restored = (
                    await second_service
                    .list_sessions()
                )

                self.assertEqual(
                    len(restored),
                    1,
                )

                repaired = (
                    await second_service
                    .repository
                    .get_conversation(
                        public_id=(
                            created.public_id
                        ),
                        owner_external_key=(
                            "development-user"
                        ),
                    )
                )

                repaired_path = Path(
                    repaired.studio_session_name
                    or ""
                )

                self.assertTrue(
                    repaired_path.exists()
                )

                self.assertEqual(
                    repaired_path.name,
                    (
                        f"{created.creature_id}"
                        ".kohakutr"
                    ),
                )

                await (
                    second_service
                    .delete_session(
                        created.public_id,
                        delete_workspace=True,
                    )
                )

            finally:
                await second_service.close()


if __name__ == "__main__":
    import unittest

    unittest.main()