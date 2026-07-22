from __future__ import annotations

import asyncio
import inspect
import shutil
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from kohakuterrarium import Studio

from app.models import SessionInfo


DEFAULT_CREATURE_REF = (
    "@kohaku-public-agent-lab/creatures/public-assistant"
)


class AgentServiceError(RuntimeError):
    """Base exception for the public Agent service layer."""


class ServiceNotStartedError(AgentServiceError):
    """Raised when an operation is attempted before service startup."""


class ServiceClosingError(AgentServiceError):
    """Raised when an operation is attempted during service shutdown."""


class PublicSessionNotFoundError(AgentServiceError):
    """Raised when a public session ID does not exist."""


class InvalidMessageError(AgentServiceError):
    """Raised when the user submits an invalid message."""


class SessionBusyError(AgentServiceError):
    """Raised when another turn is already running in the session."""


@dataclass(slots=True)
class _ManagedSession:
    """Internal mapping between public and KohakuTerrarium sessions."""

    public_id: str
    studio_session_id: str
    creature_id: str
    name: str
    workspace: Path
    created_at: datetime
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_generating: bool = False

    def snapshot(self) -> SessionInfo:
        """Create an immutable public snapshot."""
        return SessionInfo(
            public_id=self.public_id,
            studio_session_id=self.studio_session_id,
            creature_id=self.creature_id,
            name=self.name,
            workspace=self.workspace,
            created_at=self.created_at,
            is_busy=self.is_generating,
        )


class AgentService:
    """Long-running manager for public KohakuTerrarium sessions.

    One AgentService owns one Studio instance. Multiple public sessions
    run inside that Studio. Each session has an isolated workspace and
    permits at most one active generation at a time.
    """

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
        creature_ref: str = DEFAULT_CREATURE_REF,
        llm: str | None = None,
    ) -> None:
        inferred_root = Path(__file__).resolve().parents[1]

        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else inferred_root
        )

        self.workspace_root = (
            Path(workspace_root).resolve()
            if workspace_root is not None
            else self.project_root / "runtime" / "workspaces"
        )

        self.creature_ref = creature_ref
        self.llm = llm

        self._studio: Studio | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, _ManagedSession] = {}

        self._lifecycle_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()
        self._closing = False

    @property
    def is_started(self) -> bool:
        """Return whether the underlying Studio is active."""
        return self._studio is not None and not self._closing

    async def __aenter__(self) -> AgentService:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Start one long-running Studio instance."""
        async with self._lifecycle_lock:
            if self._studio is not None:
                return

            self.workspace_root.mkdir(parents=True, exist_ok=True)

            stack = AsyncExitStack()

            try:
                studio = await stack.enter_async_context(Studio())
            except BaseException:
                await stack.aclose()
                raise

            self._exit_stack = stack
            self._studio = studio
            self._closing = False

    async def close(self) -> None:
        """Stop all managed sessions and shut down Studio cleanly."""
        async with self._lifecycle_lock:
            if self._studio is None or self._closing:
                return

            self._closing = True
            studio = self._studio
            stack = self._exit_stack

        async with self._registry_lock:
            records = list(self._sessions.values())

        # First interrupt any active model generations.
        for record in records:
            await self._safe_interrupt(studio, record)

        # Then wait until every active turn releases its lock.
        for record in records:
            async with record.turn_lock:
                with suppress(Exception):
                    await studio.sessions.stop(
                        record.studio_session_id
                    )

        async with self._registry_lock:
            self._sessions.clear()

        try:
            if stack is not None:
                await stack.aclose()
            else:
                await studio.shutdown()
        finally:
            async with self._lifecycle_lock:
                self._studio = None
                self._exit_stack = None
                self._closing = False

    async def create_session(
        self,
        *,
        name: str | None = None,
    ) -> SessionInfo:
        """Create a new Agent session and isolated workspace."""
        studio = self._require_studio()

        public_id = uuid4().hex
        display_name = (
            name.strip()
            if isinstance(name, str) and name.strip()
            else f"public-session-{public_id[:8]}"
        )

        workspace = self.workspace_root / public_id
        workspace.mkdir(parents=True, exist_ok=False)

        try:
            session = await studio.sessions.start_creature(
                self.creature_ref,
                pwd=str(workspace),
                llm=self.llm,
                name=display_name,
            )

            if not session.creatures:
                raise AgentServiceError(
                    "Studio created a session without a creature."
                )

            creature_id = str(
                session.creatures[0]["creature_id"]
            )

            record = _ManagedSession(
                public_id=public_id,
                studio_session_id=str(session.session_id),
                creature_id=creature_id,
                name=display_name,
                workspace=workspace,
                created_at=datetime.now(timezone.utc),
            )

            async with self._registry_lock:
                self._sessions[public_id] = record

            return record.snapshot()

        except BaseException:
            await asyncio.to_thread(
                shutil.rmtree,
                workspace,
                ignore_errors=True,
            )
            raise

    async def get_session(
        self,
        public_id: str,
    ) -> SessionInfo:
        """Return one session's public metadata."""
        self._require_studio()
        record = await self._get_record(public_id)

        async with record.state_lock:
            return record.snapshot()

    async def list_sessions(self) -> list[SessionInfo]:
        """Return all managed sessions, oldest first."""
        self._require_studio()

        async with self._registry_lock:
            records = list(self._sessions.values())

        records.sort(key=lambda item: item.created_at)

        snapshots: list[SessionInfo] = []

        for record in records:
            async with record.state_lock:
                snapshots.append(record.snapshot())

        return snapshots

    async def stream_message(
        self,
        public_id: str,
        content: str,
    ) -> AsyncIterator[str]:
        """Send one message and yield the response incrementally."""
        studio = self._require_studio()
        record = await self._get_record(public_id)

        if not isinstance(content, str) or not content.strip():
            raise InvalidMessageError(
                "Message content must be a non-empty string."
            )

        # Atomically reserve this session for one generation.
        async with record.state_lock:
            if record.is_generating:
                raise SessionBusyError(
                    f"Session is already generating: {public_id}"
                )

            record.is_generating = True

        try:
            async with record.turn_lock:
                stream_result = studio.sessions.chat.chat(
                    record.studio_session_id,
                    record.creature_id,
                    content.strip(),
                )

                stream = await self._resolve_maybe_awaitable(
                    stream_result
                )

                completed = False

                try:
                    async for chunk in stream:
                        text = (
                            chunk
                            if isinstance(chunk, str)
                            else str(chunk)
                        )

                        if text:
                            yield text

                    completed = True

                finally:
                    # This runs when a browser disconnects, a caller
                    # cancels iteration, or the stream raises an error.
                    if not completed:
                        await self._safe_interrupt(studio, record)

        finally:
            async with record.state_lock:
                record.is_generating = False

    async def chat(
        self,
        public_id: str,
        content: str,
    ) -> str:
        """Buffered convenience wrapper around stream_message."""
        chunks: list[str] = []

        async for chunk in self.stream_message(
            public_id,
            content,
        ):
            chunks.append(chunk)

        return "".join(chunks)

    async def history(
        self,
        public_id: str,
    ) -> dict[str, Any]:
        """Return the current conversation history payload."""
        studio = self._require_studio()
        record = await self._get_record(public_id)

        result = studio.sessions.chat.history(
            record.studio_session_id,
            record.creature_id,
        )

        resolved = await self._resolve_maybe_awaitable(result)

        if isinstance(resolved, dict):
            return resolved

        return {"history": resolved}

    async def interrupt(
        self,
        public_id: str,
    ) -> None:
        """Interrupt the active generation for a session."""
        studio = self._require_studio()
        record = await self._get_record(public_id)

        await studio.sessions.ctl.interrupt(
            record.studio_session_id,
            record.creature_id,
        )

    async def delete_session(
        self,
        public_id: str,
        *,
        delete_workspace: bool = False,
    ) -> None:
        """Stop and unregister a session."""
        studio = self._require_studio()
        record = await self._get_record(public_id)

        await self._safe_interrupt(studio, record)

        async with record.turn_lock:
            await studio.sessions.stop(
                record.studio_session_id
            )

        async with self._registry_lock:
            self._sessions.pop(public_id, None)

        if delete_workspace:
            await asyncio.to_thread(
                shutil.rmtree,
                record.workspace,
                ignore_errors=True,
            )

    def _require_studio(self) -> Studio:
        if self._closing:
            raise ServiceClosingError(
                "AgentService is shutting down."
            )

        if self._studio is None:
            raise ServiceNotStartedError(
                "AgentService has not been started. "
                "Use 'async with AgentService()' or call start()."
            )

        return self._studio

    async def _get_record(
        self,
        public_id: str,
    ) -> _ManagedSession:
        if not isinstance(public_id, str) or not public_id:
            raise PublicSessionNotFoundError(
                "A valid public session ID is required."
            )

        async with self._registry_lock:
            record = self._sessions.get(public_id)

        if record is None:
            raise PublicSessionNotFoundError(
                f"Public session not found: {public_id}"
            )

        return record

    @staticmethod
    async def _resolve_maybe_awaitable(
        value: Any,
    ) -> Any:
        if inspect.isawaitable(value):
            return await value

        return value

    @staticmethod
    async def _safe_interrupt(
        studio: Studio,
        record: _ManagedSession,
    ) -> None:
        with suppress(Exception):
            await studio.sessions.ctl.interrupt(
                record.studio_session_id,
                record.creature_id,
            )