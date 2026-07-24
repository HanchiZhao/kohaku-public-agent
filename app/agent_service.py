from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from kohakuterrarium import Studio, Terrarium
from kohakuterrarium.studio.persistence.session_index import (
    close_session_index,
)

from app.models import SessionInfo
from app.persistence import (
    ConversationRecord,
    ConversationRepository,
    Database,
    RepositoryError,
)


logger = logging.getLogger(__name__)

DEFAULT_CREATURE_REF = (
    "@kohaku-public-agent-lab/creatures/public-assistant"
)
DEFAULT_OWNER_EXTERNAL_KEY = "development-user"
DEFAULT_OWNER_DISPLAY_NAME = "Development User"

SESSION_DIRECTORY_ENV = "KT_SESSION_DIR"

StudioFactory = Callable[[], Any]


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
    session_path: Path
    studio_session_id: str
    creature_id: str
    name: str
    workspace: Path
    created_at: datetime

    turn_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock
    )
    state_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock
    )
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
    """Manage public Agent sessions and persistent product mappings.

    KohakuTerrarium owns:
    - model context;
    - conversation history;
    - Agent state;
    - the physical .kohakutr session file.

    The application SQLite database owns:
    - the stable public conversation ID;
    - user ownership;
    - the conversation title;
    - the .kohakutr path;
    - the current live Studio and Creature identifiers.
    """

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
        session_root: str | Path | None = None,
        database_path: str | Path | None = None,
        database: Database | None = None,
        repository: ConversationRepository | None = None,
        creature_ref: str = DEFAULT_CREATURE_REF,
        llm: str | None = None,
        owner_external_key: str = DEFAULT_OWNER_EXTERNAL_KEY,
        owner_display_name: str = DEFAULT_OWNER_DISPLAY_NAME,
        studio_factory: StudioFactory | None = None,
    ) -> None:
        inferred_root = Path(__file__).resolve().parents[1]

        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else inferred_root
        )

        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else self.project_root
            / "runtime"
            / "workspaces"
        )

        configured_session_root = (
            session_root
            or os.getenv(SESSION_DIRECTORY_ENV)
        )

        self.session_root = (
            Path(configured_session_root)
            .expanduser()
            .resolve()
            if configured_session_root is not None
            else self.project_root
            / "runtime"
            / "kohaku_sessions"
        )

        self.creature_ref = creature_ref
        self.llm = llm

        self.owner_external_key = owner_external_key
        self.owner_display_name = owner_display_name

        self.studio_factory = studio_factory

        if repository is not None:
            if (
                database is not None
                and repository.database is not database
            ):
                raise ValueError(
                    "repository and database must reference "
                    "the same Database."
                )

            self.database = repository.database
            self.repository = repository
            self._owns_database = False

        else:
            if database is None:
                self.database = Database(
                    database_path=database_path,
                    project_root=self.project_root,
                )
                self._owns_database = True
            else:
                self.database = database
                self._owns_database = False

            self.repository = ConversationRepository(
                self.database
            )

        self._studio: Any | None = None
        self._exit_stack: AsyncExitStack | None = None

        self._sessions: dict[
            str,
            _ManagedSession,
        ] = {}

        self._lifecycle_lock = asyncio.Lock()
        self._registry_lock = asyncio.Lock()

        self._closing = False

        self._session_environment_active = False
        self._previous_session_environment_exists = False
        self._previous_session_environment_value: (
            str | None
        ) = None

    @property
    def is_started(self) -> bool:
        """Return whether Studio and SQLite are active."""

        return (
            self._studio is not None
            and self.database.is_started
            and not self._closing
        )

    async def __aenter__(
        self,
    ) -> AgentService:
        await self.start()
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

        await self.close()

    async def start(self) -> None:
        """Start SQLite, Studio and restore saved conversations."""

        async with self._lifecycle_lock:
            if self._studio is not None:
                return

            self.workspace_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.session_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            await self.database.start()

            stack = AsyncExitStack()

            try:
                await self.repository.ensure_user(
                    external_key=self.owner_external_key,
                    display_name=self.owner_display_name,
                )

                self._activate_session_directory_environment()

                studio = await stack.enter_async_context(
                    self._create_studio()
                )

                self._exit_stack = stack
                self._studio = studio
                self._closing = False

                await self._restore_persisted_sessions(
                    studio
                )

            except BaseException:
                self._studio = None
                self._exit_stack = None

                try:
                    await stack.aclose()
                finally:
                    self._close_kohaku_session_index()
                    self._restore_session_directory_environment()

                    if self._owns_database:
                        await self.database.close()

                raise

    async def close(self) -> None:
        """Stop live sessions without deleting saved conversations."""

        async with self._lifecycle_lock:
            if (
                self._studio is None
                or self._closing
            ):
                return

            self._closing = True

            studio = self._studio
            stack = self._exit_stack

        try:
            async with self._registry_lock:
                records = list(
                    self._sessions.values()
                )

            # Request cancellation before waiting on turn locks.
            for record in records:
                await self._safe_interrupt(
                    studio,
                    record,
                )

            # Stopping a live Studio session closes its handles,
            # but does not logically delete the public conversation.
            for record in records:
                async with record.turn_lock:
                    with suppress(Exception):
                        await studio.sessions.stop(
                            record.studio_session_id
                        )

            async with self._registry_lock:
                self._sessions.clear()

            if stack is not None:
                await stack.aclose()
            else:
                await studio.shutdown()

        finally:
            # Studio shutdown closes individual SessionStore objects.
            # The process-wide .kt-index.kvault singleton is separate
            # and must be released before the session directory can
            # be removed or rotated on Windows.
            self._close_kohaku_session_index()

            async with self._lifecycle_lock:
                self._studio = None
                self._exit_stack = None
                self._closing = False

            self._restore_session_directory_environment()

            if self._owns_database:
                await self.database.close()

    async def create_session(
        self,
        *,
        name: str | None = None,
    ) -> SessionInfo:
        """Create and persist one new public Agent session."""

        studio = self._require_studio()

        public_id = uuid4().hex

        display_name = (
            name.strip()
            if isinstance(name, str)
            and name.strip()
            else f"public-session-{public_id[:8]}"
        )

        workspace = (
            self.workspace_root / public_id
        )

        workspace.mkdir(
            parents=True,
            exist_ok=False,
        )

        session: Any | None = None
        creature_id: str | None = None
        session_path: Path | None = None

        try:
            session = (
                await studio.sessions.start_creature(
                    self.creature_ref,
                    pwd=str(workspace),
                    llm=self.llm,
                    name=display_name,
                )
            )

            creature_id = self._primary_creature_id(
                session
            )

            # In the currently installed KohakuTerrarium build,
            # Studio stores a standalone Creature session using
            # the Creature ID as the .kohakutr filename.
            session_path = (
                await self._wait_for_new_session_path(
                    studio,
                    creature_id,
                    str(session.session_id),
                )
            )

            created_at = datetime.now(
                timezone.utc
            )

            await self.repository.create_conversation(
                public_id=public_id,
                owner_external_key=(
                    self.owner_external_key
                ),
                title=display_name,
                workspace_path=workspace,
                studio_session_name=str(
                    session_path
                ),
                studio_session_id=str(
                    session.session_id
                ),
                creature_id=creature_id,
                created_at=created_at,
            )

            record = _ManagedSession(
                public_id=public_id,
                session_path=session_path,
                studio_session_id=str(
                    session.session_id
                ),
                creature_id=creature_id,
                name=display_name,
                workspace=workspace,
                created_at=created_at,
            )

            async with self._registry_lock:
                self._sessions[public_id] = record

            return record.snapshot()

        except RepositoryError as error:
            if session is not None:
                with suppress(Exception):
                    await studio.sessions.stop(
                        str(session.session_id)
                    )

            if session_path is None:
                session_path = (
                    self._resolve_existing_session_path(
                        studio,
                        creature_id,
                        (
                            str(session.session_id)
                            if session is not None
                            else None
                        ),
                    )
                )

            await self._safe_delete_persisted_session(
                studio,
                session_path,
            )

            await asyncio.to_thread(
                shutil.rmtree,
                workspace,
                ignore_errors=True,
            )

            raise AgentServiceError(
                "The Agent session started, but its "
                "SQLite mapping could not be saved."
            ) from error

        except BaseException:
            if session is not None:
                with suppress(Exception):
                    await studio.sessions.stop(
                        str(session.session_id)
                    )

            if session_path is None:
                session_path = (
                    self._resolve_existing_session_path(
                        studio,
                        creature_id,
                        (
                            str(session.session_id)
                            if session is not None
                            else None
                        ),
                    )
                )

            await self._safe_delete_persisted_session(
                studio,
                session_path,
            )

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

        record = await self._get_record(
            public_id
        )

        async with record.state_lock:
            return record.snapshot()

    async def list_sessions(
        self,
    ) -> list[SessionInfo]:
        """Return restored and active sessions, oldest first."""

        self._require_studio()

        async with self._registry_lock:
            records = list(
                self._sessions.values()
            )

        records.sort(
            key=lambda item: item.created_at
        )

        snapshots: list[SessionInfo] = []

        for record in records:
            async with record.state_lock:
                snapshots.append(
                    record.snapshot()
                )

        return snapshots

    async def stream_message(
        self,
        public_id: str,
        content: str,
    ) -> AsyncIterator[str]:
        """Send one message and yield response chunks."""

        studio = self._require_studio()

        record = await self._get_record(
            public_id
        )

        if (
            not isinstance(content, str)
            or not content.strip()
        ):
            raise InvalidMessageError(
                "Message content must be a "
                "non-empty string."
            )

        async with record.state_lock:
            if record.is_generating:
                raise SessionBusyError(
                    "Session is already generating: "
                    f"{public_id}"
                )

            record.is_generating = True

        try:
            async with record.turn_lock:
                stream_result = (
                    studio.sessions.chat.chat(
                        record.studio_session_id,
                        record.creature_id,
                        content.strip(),
                    )
                )

                stream = (
                    await self._resolve_maybe_awaitable(
                        stream_result
                    )
                )

                completed = False

                try:
                    async for chunk in stream:
                        text = (
                            chunk
                            if isinstance(
                                chunk,
                                str,
                            )
                            else str(chunk)
                        )

                        if text:
                            yield text

                    completed = True

                finally:
                    if not completed:
                        await self._safe_interrupt(
                            studio,
                            record,
                        )

        finally:
            async with record.state_lock:
                record.is_generating = False

    async def chat(
        self,
        public_id: str,
        content: str,
    ) -> str:
        """Buffered wrapper around stream_message()."""

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
        """Return the current conversation history."""

        studio = self._require_studio()

        record = await self._get_record(
            public_id
        )

        result = studio.sessions.chat.history(
            record.studio_session_id,
            record.creature_id,
        )

        resolved = (
            await self._resolve_maybe_awaitable(
                result
            )
        )

        if isinstance(resolved, dict):
            return resolved

        return {
            "history": resolved,
        }

    async def interrupt(
        self,
        public_id: str,
    ) -> None:
        """Interrupt the active response for one session."""

        studio = self._require_studio()

        record = await self._get_record(
            public_id
        )

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
        """Delete a public conversation and its saved Agent session."""

        studio = self._require_studio()

        record = await self._get_record(
            public_id
        )

        await self._safe_interrupt(
            studio,
            record,
        )

        try:
            await self.repository.mark_deleted(
                public_id=public_id,
                owner_external_key=(
                    self.owner_external_key
                ),
            )
        except RepositoryError as error:
            raise AgentServiceError(
                "The conversation could not be "
                "marked deleted in SQLite."
            ) from error

        try:
            async with record.turn_lock:
                with suppress(Exception):
                    await studio.sessions.stop(
                        record.studio_session_id
                    )

        finally:
            async with self._registry_lock:
                self._sessions.pop(
                    public_id,
                    None,
                )

        await self._safe_delete_persisted_session(
            studio,
            record.session_path,
        )

        if delete_workspace:
            await asyncio.to_thread(
                shutil.rmtree,
                record.workspace,
                ignore_errors=True,
            )

    async def _restore_persisted_sessions(
        self,
        studio: Any,
    ) -> None:
        """Resume every non-deleted SQLite conversation."""

        conversations = (
            await self.repository.list_conversations(
                owner_external_key=(
                    self.owner_external_key
                ),
            )
        )

        for conversation in conversations:
            live_session: Any | None = None

            try:
                session_path = (
                    self._resolve_conversation_session_path(
                        studio,
                        conversation,
                    )
                )

                workspace = self._resolve_workspace_path(
                    conversation.workspace_path
                )

                workspace.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                live_session = (
                    await studio.persistence.resume(
                        session_path,
                        pwd_override=str(
                            workspace
                        ),
                        llm=self.llm,
                    )
                )

                creature_id = (
                    self._primary_creature_id(
                        live_session
                    )
                )

                # Resolve again after resume in case KohakuTerrarium
                # migrated the store to a newer canonical filename.
                canonical_path = (
                    self._resolve_existing_session_path(
                        studio,
                        creature_id,
                        session_path,
                    )
                    or session_path
                )

                record = _ManagedSession(
                    public_id=(
                        conversation.public_id
                    ),
                    session_path=canonical_path,
                    studio_session_id=str(
                        live_session.session_id
                    ),
                    creature_id=creature_id,
                    name=conversation.title,
                    workspace=workspace,
                    created_at=(
                        conversation.created_at
                    ),
                )

                await (
                    self.repository
                    .update_runtime_binding(
                        public_id=(
                            conversation.public_id
                        ),
                        owner_external_key=(
                            self.owner_external_key
                        ),
                        studio_session_name=str(
                            canonical_path
                        ),
                        studio_session_id=(
                            record.studio_session_id
                        ),
                        creature_id=(
                            record.creature_id
                        ),
                    )
                )

                async with self._registry_lock:
                    self._sessions[
                        conversation.public_id
                    ] = record

                logger.info(
                    "Restored public conversation "
                    "%s from %s",
                    conversation.public_id,
                    canonical_path,
                )

            except Exception as error:
                if live_session is not None:
                    with suppress(Exception):
                        await studio.sessions.stop(
                            str(
                                live_session.session_id
                            )
                        )

                logger.warning(
                    "Could not restore public "
                    "conversation %s: %s",
                    conversation.public_id,
                    error,
                )

                with suppress(Exception):
                    await (
                        self.repository
                        .mark_recovery_failed(
                            public_id=(
                                conversation.public_id
                            ),
                            owner_external_key=(
                                self.owner_external_key
                            ),
                            error=(
                                f"{error.__class__.__name__}: "
                                f"{error}"
                            ),
                        )
                    )

    def _create_studio(self) -> Any:
        """Create the default persistent Studio or a supplied fake."""

        if self.studio_factory is not None:
            return self.studio_factory()

        engine = Terrarium(
            session_dir=str(
                self.session_root
            )
        )

        return Studio(
            engine=engine
        )

    @staticmethod
    def _close_kohaku_session_index() -> None:
        """Release KohakuTerrarium's process-wide session index.

        Studio shutdown closes individual .kohakutr stores, but the
        shared .kt-index.kvault sidecar is a process-wide singleton and
        must be closed separately. This is especially important on
        Windows, where an open SQLite handle prevents temporary session
        directories from being deleted.
        """

        try:
            close_session_index()
        except Exception:
            logger.exception(
                "Failed to close KohakuTerrarium session index."
            )

    def _activate_session_directory_environment(
        self,
    ) -> None:
        """Point Studio's persistence helpers at the app session root."""

        if self._session_environment_active:
            return

        self._previous_session_environment_exists = (
            SESSION_DIRECTORY_ENV in os.environ
        )

        self._previous_session_environment_value = (
            os.environ.get(
                SESSION_DIRECTORY_ENV
            )
        )

        os.environ[
            SESSION_DIRECTORY_ENV
        ] = str(self.session_root)

        self._session_environment_active = True

    def _restore_session_directory_environment(
        self,
    ) -> None:
        """Restore the process environment changed during start()."""

        if not self._session_environment_active:
            return

        if self._previous_session_environment_exists:
            previous = (
                self._previous_session_environment_value
            )

            if previous is not None:
                os.environ[
                    SESSION_DIRECTORY_ENV
                ] = previous
            else:
                os.environ.pop(
                    SESSION_DIRECTORY_ENV,
                    None,
                )
        else:
            os.environ.pop(
                SESSION_DIRECTORY_ENV,
                None,
            )

        self._previous_session_environment_exists = False
        self._previous_session_environment_value = None
        self._session_environment_active = False

    async def _wait_for_new_session_path(
        self,
        studio: Any,
        *identifiers: str | Path | None,
    ) -> Path:
        """Wait briefly for Studio to publish the session file."""

        for _ in range(40):
            resolved = (
                self._resolve_existing_session_path(
                    studio,
                    *identifiers,
                )
            )

            if resolved is not None:
                return resolved

            await asyncio.sleep(0.05)

        identifiers_text = ", ".join(
            str(identifier)
            for identifier in identifiers
            if identifier
        )

        raise AgentServiceError(
            "KohakuTerrarium created the live session, "
            "but no .kohakutr file was found in "
            f"{self.session_root}. "
            f"Identifiers checked: {identifiers_text}"
        )

    def _resolve_conversation_session_path(
        self,
        studio: Any,
        conversation: ConversationRecord,
    ) -> Path:
        """Resolve current and legacy SQLite mappings."""

        resolved = self._resolve_existing_session_path(
            studio,

            # New v0.5 format: a complete .kohakutr path.
            conversation.studio_session_name,

            # Compatibility with earlier failed implementations.
            conversation.creature_id,
            conversation.studio_session_id,
        )

        if resolved is None:
            raise AgentServiceError(
                "No saved KohakuTerrarium session file "
                "could be resolved for public conversation "
                f"{conversation.public_id}. "
                "Stored mapping: "
                f"{conversation.studio_session_name!r}; "
                f"Creature ID: {conversation.creature_id!r}; "
                f"Studio ID: {conversation.studio_session_id!r}."
            )

        return resolved

    def _resolve_existing_session_path(
        self,
        studio: Any,
        *identifiers: str | Path | None,
    ) -> Path | None:
        """Resolve a real session file from paths, stems or old IDs."""

        lookup_values: list[str] = []

        for identifier in identifiers:
            if identifier is None:
                continue

            text = str(identifier).strip()

            if not text:
                continue

            if text not in lookup_values:
                lookup_values.append(text)

        for value in lookup_values:
            supplied_path = Path(
                value
            ).expanduser()

            direct_candidates: list[Path] = []

            if supplied_path.is_absolute():
                direct_candidates.append(
                    supplied_path
                )

            else:
                direct_candidates.append(
                    self.session_root
                    / supplied_path
                )

            if (
                supplied_path.suffix
                != ".kohakutr"
            ):
                direct_candidates.append(
                    self.session_root
                    / f"{value}.kohakutr"
                )

            for candidate in direct_candidates:
                if candidate.exists():
                    return candidate.resolve()

            # Studio's public resolver searches KT_SESSION_DIR.
            resolver = getattr(
                studio.persistence,
                "resolve_path",
                None,
            )

            if callable(resolver):
                resolver_keys = [
                    value,
                    supplied_path.name,
                    supplied_path.stem,
                ]

                for key in resolver_keys:
                    if not key:
                        continue

                    with suppress(Exception):
                        resolved = resolver(
                            str(key)
                        )

                        if resolved is not None:
                            path = Path(
                                resolved
                            ).expanduser()

                            if path.exists():
                                return path.resolve()

        return None

    def _require_studio(self) -> Any:
        if self._closing:
            raise ServiceClosingError(
                "AgentService is shutting down."
            )

        if self._studio is None:
            raise ServiceNotStartedError(
                "AgentService has not been started. "
                "Use 'async with AgentService()' "
                "or call start()."
            )

        return self._studio

    async def _get_record(
        self,
        public_id: str,
    ) -> _ManagedSession:
        if (
            not isinstance(public_id, str)
            or not public_id
        ):
            raise PublicSessionNotFoundError(
                "A valid public session ID "
                "is required."
            )

        async with self._registry_lock:
            record = self._sessions.get(
                public_id
            )

        if record is None:
            raise PublicSessionNotFoundError(
                "Public session not found: "
                f"{public_id}"
            )

        return record

    def _resolve_workspace_path(
        self,
        value: str | Path,
    ) -> Path:
        path = Path(value).expanduser()

        if not path.is_absolute():
            path = self.project_root / path

        return path.resolve()

    @staticmethod
    def _primary_creature_id(
        session: Any,
    ) -> str:
        creatures = getattr(
            session,
            "creatures",
            None,
        )

        if not creatures:
            raise AgentServiceError(
                "Studio created or restored "
                "a session without a Creature."
            )

        creature = creatures[0]

        if isinstance(creature, dict):
            creature_id = creature.get(
                "creature_id"
            )
        else:
            creature_id = getattr(
                creature,
                "creature_id",
                None,
            )

        if not creature_id:
            raise AgentServiceError(
                "The Studio session has "
                "no Creature ID."
            )

        return str(creature_id)

    @staticmethod
    async def _resolve_maybe_awaitable(
        value: Any,
    ) -> Any:
        if inspect.isawaitable(value):
            return await value

        return value

    @staticmethod
    async def _safe_interrupt(
        studio: Any,
        record: _ManagedSession,
    ) -> None:
        with suppress(Exception):
            await studio.sessions.ctl.interrupt(
                record.studio_session_id,
                record.creature_id,
            )

    @staticmethod
    async def _safe_delete_persisted_session(
        studio: Any,
        session_path: Path | None,
    ) -> None:
        if session_path is None:
            return

        # The public delete API resolves a session family by its stem
        # inside KT_SESSION_DIR and removes SQLite sidecar files too.
        session_stem = session_path.stem

        with suppress(Exception):
            await asyncio.to_thread(
                studio.persistence.delete,
                session_stem,
            )