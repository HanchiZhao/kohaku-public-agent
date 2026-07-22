from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from fastapi.testclient import TestClient

from app.agent_service import (
    PublicSessionNotFoundError,
)
from app.main import create_app
from app.models import SessionInfo


class FakeAgentService:
    """In-memory replacement used by API tests.

    It verifies the HTTP layer without loading Studio or calling a model.
    """

    def __init__(self) -> None:
        self._started = False
        self._counter = 0
        self._sessions: dict[str, SessionInfo] = {}

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        self._started = True

    async def close(self) -> None:
        self._started = False
        self._sessions.clear()

    async def create_session(
        self,
        *,
        name: str | None = None,
    ) -> SessionInfo:
        self._counter += 1
        public_id = f"public-test-{self._counter}"

        session = SessionInfo(
            public_id=public_id,
            studio_session_id=(
                f"studio-internal-{self._counter}"
            ),
            creature_id=(
                f"creature-internal-{self._counter}"
            ),
            name=name or f"Test session {self._counter}",
            workspace=Path(
                f"runtime/workspaces/{public_id}"
            ),
            created_at=datetime.now(timezone.utc),
            is_busy=False,
        )

        self._sessions[public_id] = session
        return session

    async def list_sessions(
        self,
    ) -> list[SessionInfo]:
        return list(self._sessions.values())

    async def get_session(
        self,
        public_id: str,
    ) -> SessionInfo:
        session = self._sessions.get(public_id)

        if session is None:
            raise PublicSessionNotFoundError(
                f"Public session not found: {public_id}"
            )

        return session

    async def chat(
        self,
        public_id: str,
        content: str,
    ) -> str:
        await self.get_session(public_id)
        return f"Echo: {content}"

    async def history(
        self,
        public_id: str,
    ) -> dict[str, object]:
        await self.get_session(public_id)

        return {
            "messages": [
                {
                    "role": "user",
                    "content": "Test message",
                },
                {
                    "role": "assistant",
                    "content": "Test response",
                },
            ]
        }

    async def delete_session(
        self,
        public_id: str,
        *,
        delete_workspace: bool = False,
    ) -> None:
        del delete_workspace

        await self.get_session(public_id)
        self._sessions.pop(public_id)


class AgentApiTests(TestCase):
    """Tests for the first non-streaming HTTP API."""

    def setUp(self) -> None:
        self.service = FakeAgentService()

        self.application = create_app(
            service_factory=lambda: self.service  # type: ignore[arg-type]
        )

        self.client = TestClient(self.application)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def _create_session(
        self,
        name: str = "Browser test",
    ) -> str:
        response = self.client.post(
            "/api/v1/sessions",
            json={"name": name},
        )

        self.assertEqual(response.status_code, 201)
        return str(response.json()["session_id"])

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(
            response.json()["agent_service_started"]
        )

    def test_create_session_hides_internal_fields(self) -> None:
        response = self.client.post(
            "/api/v1/sessions",
            json={"name": "Public test"},
        )

        self.assertEqual(response.status_code, 201)

        payload = response.json()

        self.assertEqual(payload["name"], "Public test")
        self.assertIn("session_id", payload)
        self.assertNotIn("studio_session_id", payload)
        self.assertNotIn("creature_id", payload)
        self.assertNotIn("workspace", payload)

    def test_list_and_get_sessions(self) -> None:
        session_id = self._create_session()

        list_response = self.client.get(
            "/api/v1/sessions"
        )

        self.assertEqual(
            list_response.status_code,
            200,
        )
        self.assertEqual(
            list_response.json()["total"],
            1,
        )

        get_response = self.client.get(
            f"/api/v1/sessions/{session_id}"
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(
            get_response.json()["session_id"],
            session_id,
        )

    def test_send_message(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"content": "Hello"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["response"],
            "Echo: Hello",
        )

    def test_empty_message_is_rejected(self) -> None:
        session_id = self._create_session()

        response = self.client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"content": "   "},
        )

        self.assertEqual(response.status_code, 422)

    def test_history_endpoint(self) -> None:
        session_id = self._create_session()

        response = self.client.get(
            f"/api/v1/sessions/{session_id}/history"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["session_id"],
            session_id,
        )
        self.assertIn(
            "messages",
            response.json()["history"],
        )

    def test_delete_session(self) -> None:
        session_id = self._create_session()

        delete_response = self.client.delete(
            f"/api/v1/sessions/{session_id}"
        )

        self.assertEqual(
            delete_response.status_code,
            204,
        )

        get_response = self.client.get(
            f"/api/v1/sessions/{session_id}"
        )

        self.assertEqual(get_response.status_code, 404)

    def test_unknown_session_returns_404(self) -> None:
        response = self.client.get(
            "/api/v1/sessions/does-not-exist"
        )

        self.assertEqual(response.status_code, 404)