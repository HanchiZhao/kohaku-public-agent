from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Information about one managed Agent session."""

    public_id: str
    studio_session_id: str
    creature_id: str
    name: str
    workspace: Path
    created_at: datetime
    is_busy: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        """Return fields that are safe to expose through a public API."""
        return {
            "session_id": self.public_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "is_busy": self.is_busy,
        }