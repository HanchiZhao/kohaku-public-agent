from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.database import Database
from app.persistence.records import ConversationRecord, UserRecord
from app.persistence.tables import AppUserRow, ConversationRow


ACTIVE_STATUS = "active"
DELETED_STATUS = "deleted"
RECOVERY_FAILED_STATUS = "recovery_failed"


class RepositoryError(RuntimeError):
    """Base repository error."""


class ConversationAlreadyExistsError(RepositoryError):
    """Raised for a duplicate public conversation ID."""


class ConversationNotFoundError(RepositoryError):
    """Raised when a conversation is missing or belongs to another user."""


class UserNotFoundError(RepositoryError):
    """Raised when an application user is missing."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


class ConversationRepository:
    """Persist product users and public-to-Studio session mappings."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def ensure_user(
        self,
        *,
        external_key: str,
        display_name: str,
    ) -> UserRecord:
        external_key = self._required(
            external_key,
            "external_key",
            120,
        )
        display_name = self._required(
            display_name,
            "display_name",
            120,
        )

        async with self.database.session() as session:
            row = await session.scalar(
                select(AppUserRow).where(
                    AppUserRow.external_key == external_key
                )
            )

            if row is not None:
                if row.display_name != display_name:
                    row.display_name = display_name
                    row.updated_at = utc_now()
                    await session.commit()

                return self._user_record(row)

            now = utc_now()

            row = AppUserRow(
                id=uuid4().hex,
                external_key=external_key,
                display_name=display_name,
                created_at=now,
                updated_at=now,
            )

            session.add(row)

            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

                row = await session.scalar(
                    select(AppUserRow).where(
                        AppUserRow.external_key == external_key
                    )
                )

                if row is None:
                    raise

            return self._user_record(row)

    async def get_user(
        self,
        *,
        external_key: str,
    ) -> UserRecord:
        async with self.database.session() as session:
            row = await session.scalar(
                select(AppUserRow).where(
                    AppUserRow.external_key == external_key
                )
            )

            if row is None:
                raise UserNotFoundError(
                    f"Application user not found: {external_key}"
                )

            return self._user_record(row)

    async def create_conversation(
        self,
        *,
        public_id: str,
        owner_external_key: str,
        title: str,
        workspace_path: str | Path,
        studio_session_name: str | None = None,
        studio_session_id: str | None = None,
        creature_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ConversationRecord:
        owner = await self.get_user(
            external_key=owner_external_key
        )

        public_id = self._required(
            public_id,
            "public_id",
            64,
        )
        title = self._required(
            title,
            "title",
            200,
        )

        timestamp = as_utc(created_at) or utc_now()

        row = ConversationRow(
            public_id=public_id,
            owner_id=owner.id,
            title=title,
            studio_session_name=self._optional(
                studio_session_name,
                255,
            ),
            studio_session_id=self._optional(
                studio_session_id,
                255,
            ),
            creature_id=self._optional(
                creature_id,
                255,
            ),
            workspace_path=str(Path(workspace_path)),
            status=ACTIVE_STATUS,
            recovery_error=None,
            created_at=timestamp,
            updated_at=timestamp,
            last_attached_at=(
                timestamp if studio_session_id else None
            ),
            deleted_at=None,
        )

        async with self.database.session() as session:
            session.add(row)

            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()

                raise ConversationAlreadyExistsError(
                    f"Conversation already exists: {public_id}"
                ) from error

        return self._conversation_record(
            row,
            owner.external_key,
        )

    async def get_conversation(
        self,
        *,
        public_id: str,
        owner_external_key: str,
        include_deleted: bool = False,
    ) -> ConversationRecord:
        async with self.database.session() as session:
            row, owner_key = await self._find_conversation(
                session,
                public_id=public_id,
                owner_external_key=owner_external_key,
                include_deleted=include_deleted,
            )

            return self._conversation_record(
                row,
                owner_key,
            )

    async def list_conversations(
        self,
        *,
        owner_external_key: str,
        include_deleted: bool = False,
    ) -> list[ConversationRecord]:
        statement = (
            select(
                ConversationRow,
                AppUserRow.external_key,
            )
            .join(
                AppUserRow,
                ConversationRow.owner_id == AppUserRow.id,
            )
            .where(
                AppUserRow.external_key == owner_external_key
            )
            .order_by(
                ConversationRow.created_at.asc()
            )
        )

        if not include_deleted:
            statement = statement.where(
                ConversationRow.deleted_at.is_(None)
            )

        async with self.database.session() as session:
            rows = (
                await session.execute(statement)
            ).all()

        return [
            self._conversation_record(
                row,
                owner_key,
            )
            for row, owner_key in rows
        ]

    async def update_runtime_binding(
        self,
        *,
        public_id: str,
        owner_external_key: str,
        studio_session_name: str | None,
        studio_session_id: str,
        creature_id: str,
    ) -> ConversationRecord:
        async with self.database.session() as session:
            row, owner_key = await self._find_conversation(
                session,
                public_id=public_id,
                owner_external_key=owner_external_key,
            )

            now = utc_now()

            row.studio_session_name = self._optional(
                studio_session_name,
                255,
            )
            row.studio_session_id = self._required(
                studio_session_id,
                "studio_session_id",
                255,
            )
            row.creature_id = self._required(
                creature_id,
                "creature_id",
                255,
            )
            row.status = ACTIVE_STATUS
            row.recovery_error = None
            row.updated_at = now
            row.last_attached_at = now
            row.deleted_at = None

            await session.commit()

            return self._conversation_record(
                row,
                owner_key,
            )

    async def mark_recovery_failed(
        self,
        *,
        public_id: str,
        owner_external_key: str,
        error: str,
    ) -> ConversationRecord:
        async with self.database.session() as session:
            row, owner_key = await self._find_conversation(
                session,
                public_id=public_id,
                owner_external_key=owner_external_key,
            )

            row.status = RECOVERY_FAILED_STATUS
            row.recovery_error = self._required(
                error,
                "error",
                4000,
            )
            row.updated_at = utc_now()

            await session.commit()

            return self._conversation_record(
                row,
                owner_key,
            )

    async def mark_deleted(
        self,
        *,
        public_id: str,
        owner_external_key: str,
    ) -> ConversationRecord:
        async with self.database.session() as session:
            row, owner_key = await self._find_conversation(
                session,
                public_id=public_id,
                owner_external_key=owner_external_key,
            )

            now = utc_now()

            row.status = DELETED_STATUS
            row.deleted_at = now
            row.updated_at = now

            await session.commit()

            return self._conversation_record(
                row,
                owner_key,
            )

    @staticmethod
    async def _find_conversation(
        session: AsyncSession,
        *,
        public_id: str,
        owner_external_key: str,
        include_deleted: bool = False,
    ) -> tuple[ConversationRow, str]:
        statement = (
            select(
                ConversationRow,
                AppUserRow.external_key,
            )
            .join(
                AppUserRow,
                ConversationRow.owner_id == AppUserRow.id,
            )
            .where(
                ConversationRow.public_id == public_id,
                AppUserRow.external_key == owner_external_key,
            )
        )

        if not include_deleted:
            statement = statement.where(
                ConversationRow.deleted_at.is_(None)
            )

        result = (
            await session.execute(statement)
        ).first()

        if result is None:
            raise ConversationNotFoundError(
                f"Conversation not found: {public_id}"
            )

        return result

    @staticmethod
    def _user_record(
        row: AppUserRow,
    ) -> UserRecord:
        return UserRecord(
            id=row.id,
            external_key=row.external_key,
            display_name=row.display_name,
            created_at=(
                as_utc(row.created_at) or utc_now()
            ),
            updated_at=(
                as_utc(row.updated_at) or utc_now()
            ),
        )

    @staticmethod
    def _conversation_record(
        row: ConversationRow,
        owner_key: str,
    ) -> ConversationRecord:
        return ConversationRecord(
            public_id=row.public_id,
            owner_id=row.owner_id,
            owner_external_key=owner_key,
            title=row.title,
            studio_session_name=row.studio_session_name,
            studio_session_id=row.studio_session_id,
            creature_id=row.creature_id,
            workspace_path=Path(row.workspace_path),
            status=row.status,
            recovery_error=row.recovery_error,
            created_at=(
                as_utc(row.created_at) or utc_now()
            ),
            updated_at=(
                as_utc(row.updated_at) or utc_now()
            ),
            last_attached_at=as_utc(
                row.last_attached_at
            ),
            deleted_at=as_utc(
                row.deleted_at
            ),
        )

    @staticmethod
    def _required(
        value: str,
        name: str,
        limit: int,
    ) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{name} must be a non-empty string."
            )

        normalized = value.strip()

        if len(normalized) > limit:
            raise ValueError(
                f"{name} exceeds {limit} characters."
            )

        return normalized

    @staticmethod
    def _optional(
        value: str | None,
        limit: int,
    ) -> str | None:
        if value is None or not value.strip():
            return None

        normalized = value.strip()

        if len(normalized) > limit:
            raise ValueError(
                f"Value exceeds {limit} characters."
            )

        return normalized