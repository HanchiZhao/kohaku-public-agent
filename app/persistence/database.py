from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.persistence.tables import Base


DATABASE_URL_ENV = "PUBLIC_AGENT_DATABASE_URL"


class DatabaseNotStartedError(RuntimeError):
    """Raised when the database is used before start()."""


def sqlite_url_from_path(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    return f"sqlite+aiosqlite:///{resolved.as_posix()}"


class Database:
    """Own the async SQLAlchemy engine and session factory."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        database_path: str | Path | None = None,
        project_root: str | Path | None = None,
        echo: bool = False,
    ) -> None:
        if database_url and database_path:
            raise ValueError(
                "Pass either database_url or database_path, not both."
            )

        root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )

        self.database_url = (
            database_url
            or (
                sqlite_url_from_path(database_path)
                if database_path is not None
                else os.getenv(DATABASE_URL_ENV)
            )
            or sqlite_url_from_path(
                root / "runtime" / "data" / "public_agent.db"
            )
        )
        self.echo = echo
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @property
    def is_started(self) -> bool:
        return self._engine is not None

    async def start(self) -> None:
        if self._engine is not None:
            return

        self._create_parent_directory()
        options: dict[str, object] = {"echo": self.echo}

        if self.database_url == "sqlite+aiosqlite:///:memory:":
            options.update(
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )

        engine = create_async_engine(self.database_url, **options)
        self._configure_sqlite(engine)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        except BaseException:
            await engine.dispose()
            raise

        self._engine = engine
        self._sessions = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def close(self) -> None:
        engine = self._engine
        self._engine = None
        self._sessions = None

        if engine is not None:
            await engine.dispose()

    async def ping(self) -> bool:
        async with self.session() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar_one() == 1

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessions is None:
            raise DatabaseNotStartedError(
                "Database is not started. Call start() first."
            )

        async with self._sessions() as session:
            yield session

    def _create_parent_directory(self) -> None:
        prefix = "sqlite+aiosqlite:///"

        if not self.database_url.startswith(prefix):
            return

        if self.database_url.endswith(":memory:"):
            return

        Path(self.database_url.removeprefix(prefix)).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _configure_sqlite(self, engine: AsyncEngine) -> None:
        if not self.database_url.startswith("sqlite+aiosqlite:"):
            return

        is_memory = self.database_url.endswith(":memory:")

        @event.listens_for(engine.sync_engine, "connect")
        def set_pragmas(connection: object, record: object) -> None:
            del record
            cursor = connection.cursor()  # type: ignore[attr-defined]

            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")

                if not is_memory:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()