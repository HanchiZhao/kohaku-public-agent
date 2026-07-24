from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.persistence import (
    ConversationRepository,
    Database,
)


async def main() -> None:
    """Run a persistence smoke test without calling a language model."""

    print("=" * 60)
    print("Starting application persistence smoke test")
    print("=" * 60)

    with TemporaryDirectory(
        prefix="kohaku-public-agent-db-"
    ) as temporary_directory:
        database_path = (
            Path(temporary_directory)
            / "public_agent.db"
        )
        public_id = uuid4().hex

        first_database = Database(
            database_path=database_path
        )
        await first_database.start()

        try:
            repository = ConversationRepository(
                first_database
            )

            user = await repository.ensure_user(
                external_key="development-user",
                display_name="Development User",
            )

            created = await repository.create_conversation(
                public_id=public_id,
                owner_external_key=user.external_key,
                title="Persistence smoke test",
                workspace_path=(
                    Path(temporary_directory)
                    / "workspaces"
                    / public_id
                ),
                studio_session_name="smoke-session",
                studio_session_id="studio-session-1",
                creature_id="creature-1",
            )

            print(
                f"Created conversation: "
                f"{created.public_id}"
            )
            print(
                f"Database ping: "
                f"{await first_database.ping()}"
            )

        finally:
            await first_database.close()

        second_database = Database(
            database_path=database_path
        )
        await second_database.start()

        try:
            repository = ConversationRepository(
                second_database
            )

            restored = await repository.get_conversation(
                public_id=public_id,
                owner_external_key="development-user",
            )

            if restored.title != "Persistence smoke test":
                raise RuntimeError(
                    "Conversation title changed "
                    "after database restart."
                )

            if (
                restored.studio_session_id
                != "studio-session-1"
            ):
                raise RuntimeError(
                    "Studio session mapping "
                    "was not persisted."
                )

            print(
                f"Restored conversation: "
                f"{restored.public_id}"
            )
            print(
                f"Restored status: "
                f"{restored.status}"
            )

            await repository.mark_deleted(
                public_id=public_id,
                owner_external_key="development-user",
            )

            active = await repository.list_conversations(
                owner_external_key="development-user",
            )

            if active:
                raise RuntimeError(
                    "Soft-deleted conversation "
                    "is still active."
                )

            print("Soft deletion verified.")

        finally:
            await second_database.close()

    print("=" * 60)
    print(
        "Application persistence smoke test "
        "completed successfully"
    )
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())