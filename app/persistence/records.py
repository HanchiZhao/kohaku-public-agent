from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UserRecord:
    """Application-level user metadata."""

    id: str
    external_key: str
    display_name: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """Persistent product metadata for one public conversation."""

    public_id: str
    owner_id: str
    owner_external_key: str
    title: str
    studio_session_name: str | None
    studio_session_id: str | None
    creature_id: str | None
    workspace_path: Path
    status: str
    recovery_error: str | None
    created_at: datetime
    updated_at: datetime
    last_attached_at: datetime | None
    deleted_at: datetime | None