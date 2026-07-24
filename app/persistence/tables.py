from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for application-owned database tables."""


class AppUserRow(Base):
    """One product-level user known to the public Agent application."""

    __tablename__ = "app_users"

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )
    external_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        unique=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ConversationRow(Base):
    """Product metadata that maps a public ID to a Studio session."""

    __tablename__ = "conversations"

    public_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    owner_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("app_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    studio_session_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    studio_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    creature_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    workspace_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    recovery_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_attached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


Index(
    "ix_conversations_owner_created",
    ConversationRow.owner_id,
    ConversationRow.created_at,
)
Index(
    "ix_conversations_owner_active",
    ConversationRow.owner_id,
    ConversationRow.deleted_at,
)