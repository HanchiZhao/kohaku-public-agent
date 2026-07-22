from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from app.models import SessionInfo


class SessionInfoTests(TestCase):
    """Tests for the public session metadata model."""

    def test_to_dict_only_exposes_public_fields(self) -> None:
        created_at = datetime(
            2026,
            7,
            22,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )

        session = SessionInfo(
            public_id="public-123",
            studio_session_id="graph-internal-456",
            creature_id="creature-internal-789",
            name="Test session",
            workspace=Path("runtime/workspaces/public-123"),
            created_at=created_at,
            is_busy=False,
        )

        payload = session.to_dict()

        self.assertEqual(payload["session_id"], "public-123")
        self.assertEqual(payload["name"], "Test session")
        self.assertEqual(
            payload["created_at"],
            "2026-07-22T12:00:00+00:00",
        )
        self.assertFalse(payload["is_busy"])

        self.assertNotIn("studio_session_id", payload)
        self.assertNotIn("creature_id", payload)
        self.assertNotIn("workspace", payload)