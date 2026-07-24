from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from app.persistence import (
    ACTIVE_STATUS,
    DELETED_STATUS,
    ConversationAlreadyExistsError,
    ConversationNotFoundError,
    ConversationRepository,
    Database,
)


class PersistenceTests(IsolatedAsyncioTestCase):
    """Verify application-owned SQLite registry behavior."""

    async def asyncSetUp(self) -> None:
        self.temporary_directory = TemporaryDirectory(
            prefix="kohaku-public-agent-test-db-"
        )
        self.database_path = (
            Path(self.temporary_directory.name)
            / "public_agent.db"
        )

        self.database = Database(
            database_path=self.database_path
        )
        await self.database.start()

        self.repository = ConversationRepository(
            self.database
        )

        self.user = await self.repository.ensure_user(
            external_key="development-user",
            display_name="Development User",
        )

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self.temporary_directory.cleanup()

    async def test_user_creation_is_idempotent(
        self,
    ) -> None:
        repeated = await self.repository.ensure_user(
            external_key="development-user",
            display_name="Development User Renamed",
        )

        self.assertEqual(
            repeated.id,
            self.user.id,
        )
        self.assertEqual(
            repeated.display_name,
            "Development User Renamed",
        )

    async def test_conversation_lifecycle(
        self,
    ) -> None:
        created = await self.repository.create_conversation(
            public_id="public-1",
            owner_external_key="development-user",
            title="First conversation",
            workspace_path=(
                "runtime/workspaces/public-1"
            ),
            studio_session_name="saved-session-1",
            studio_session_id="studio-1",
            creature_id="creature-1",
        )

        self.assertEqual(
            created.status,
            ACTIVE_STATUS,
        )
        self.assertEqual(
            created.studio_session_id,
            "studio-1",
        )

        fetched = await self.repository.get_conversation(
            public_id="public-1",
            owner_external_key="development-user",
        )

        self.assertEqual(
            fetched.title,
            "First conversation",
        )

        updated = (
            await self.repository.update_runtime_binding(
                public_id="public-1",
                owner_external_key="development-user",
                studio_session_name="saved-session-1",
                studio_session_id="studio-2",
                creature_id="creature-2",
            )
        )

        self.assertEqual(
            updated.studio_session_id,
            "studio-2",
        )
        self.assertEqual(
            updated.creature_id,
            "creature-2",
        )
        self.assertIsNotNone(
            updated.last_attached_at
        )

        deleted = await self.repository.mark_deleted(
            public_id="public-1",
            owner_external_key="development-user",
        )

        self.assertEqual(
            deleted.status,
            DELETED_STATUS,
        )
        self.assertIsNotNone(
            deleted.deleted_at
        )

        active_rows = (
            await self.repository.list_conversations(
                owner_external_key="development-user",
            )
        )

        self.assertEqual(
            active_rows,
            [],
        )

        all_rows = (
            await self.repository.list_conversations(
                owner_external_key="development-user",
                include_deleted=True,
            )
        )

        self.assertEqual(
            len(all_rows),
            1,
        )

    async def test_duplicate_public_id_is_rejected(
        self,
    ) -> None:
        keyword_arguments = {
            "public_id": "duplicate-public-id",
            "owner_external_key": "development-user",
            "title": "Duplicate test",
            "workspace_path": (
                "runtime/workspaces/"
                "duplicate-public-id"
            ),
        }

        await self.repository.create_conversation(
            **keyword_arguments
        )

        with self.assertRaises(
            ConversationAlreadyExistsError
        ):
            await self.repository.create_conversation(
                **keyword_arguments
            )

    async def test_owner_isolation(
        self,
    ) -> None:
        await self.repository.ensure_user(
            external_key="second-user",
            display_name="Second User",
        )

        await self.repository.create_conversation(
            public_id="private-conversation",
            owner_external_key="development-user",
            title="Private conversation",
            workspace_path=(
                "runtime/workspaces/"
                "private-conversation"
            ),
        )

        with self.assertRaises(
            ConversationNotFoundError
        ):
            await self.repository.get_conversation(
                public_id="private-conversation",
                owner_external_key="second-user",
            )

    async def test_rows_survive_database_restart(
        self,
    ) -> None:
        await self.repository.create_conversation(
            public_id="restart-test",
            owner_external_key="development-user",
            title="Restart test",
            workspace_path=(
                "runtime/workspaces/restart-test"
            ),
            studio_session_name=(
                "saved-restart-test"
            ),
            studio_session_id=(
                "studio-before-restart"
            ),
            creature_id=(
                "creature-before-restart"
            ),
        )

        await self.database.close()

        self.database = Database(
            database_path=self.database_path
        )
        await self.database.start()

        self.repository = ConversationRepository(
            self.database
        )

        restored = await self.repository.get_conversation(
            public_id="restart-test",
            owner_external_key="development-user",
        )

        self.assertEqual(
            restored.title,
            "Restart test",
        )
        self.assertEqual(
            restored.studio_session_id,
            "studio-before-restart",
        )
        self.assertEqual(
            restored.creature_id,
            "creature-before-restart",
        )