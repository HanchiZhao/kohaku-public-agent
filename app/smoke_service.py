from __future__ import annotations

import asyncio
import gc
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from app.agent_service import AgentService
from app.smoke_restart_recovery import (
    cleanup_directory_with_retries,
)


TEST_CODE = "KOHAKU-SERVICE-2026"


async def collect_stream(
    stream: AsyncIterator[str],
) -> str:
    """Collect and display a streaming response."""

    chunks: list[str] = []

    async for chunk in stream:
        print(
            chunk,
            end="",
            flush=True,
        )
        chunks.append(chunk)

    print()

    return "".join(chunks)


async def main() -> None:
    """Run a real multi-turn AgentService smoke test.

    The test uses an isolated temporary database, workspace directory
    and KohakuTerrarium session directory. On Windows, final cleanup is
    retried because the session history index may briefly retain an
    open file handle after Studio shutdown.
    """

    print("=" * 68)
    print(
        "Starting long-running "
        "AgentService smoke test"
    )
    print("=" * 68)

    temporary_root = Path(
        tempfile.mkdtemp(
            prefix="kohaku-service-smoke-"
        )
    ).resolve()

    database_path = (
        temporary_root
        / "runtime"
        / "data"
        / "agent.db"
    )

    workspace_root = (
        temporary_root
        / "runtime"
        / "workspaces"
    )

    session_root = (
        temporary_root
        / "runtime"
        / "kohaku_sessions"
    )

    service: AgentService | None = None
    public_id: str | None = None

    test_completed = False
    cleanup_succeeded = False

    try:
        print(
            f"Temporary root: "
            f"{temporary_root}"
        )

        service = AgentService(
            project_root=temporary_root,
            workspace_root=workspace_root,
            session_root=session_root,
            database_path=database_path,
        )

        await service.start()

        print(
            f"Service started: "
            f"{service.is_started}"
        )

        session = await service.create_session(
            name="agent-service-smoke-test"
        )

        public_id = session.public_id

        print(
            f"Public session ID: "
            f"{session.public_id}"
        )

        print(
            f"Studio session ID: "
            f"{session.studio_session_id}"
        )

        print(
            f"Creature ID: "
            f"{session.creature_id}"
        )

        print(
            f"Workspace: "
            f"{session.workspace}"
        )

        sessions = await service.list_sessions()

        if len(sessions) != 1:
            raise RuntimeError(
                "Expected exactly one "
                "managed session."
            )

        print()
        print(
            "Turn 1 - storing a value"
        )
        print("-" * 68)

        response_one = await service.chat(
            session.public_id,
            (
                f"请记住测试代号 {TEST_CODE}。"
                "这是一次多轮会话测试。"
                "请只回复：已记住"
            ),
        )

        if not response_one.strip():
            raise RuntimeError(
                "The first Agent response "
                "was empty."
            )

        print(response_one)

        print()
        print(
            "Turn 2 - verifying "
            "conversation memory"
        )
        print("-" * 68)

        response_two = await collect_stream(
            service.stream_message(
                session.public_id,
                (
                    "请只回复我上一条消息中的"
                    "测试代号，不要添加解释"
                    "或其他文字。"
                ),
            )
        )

        if TEST_CODE not in response_two:
            raise RuntimeError(
                "The second response did not "
                "contain the expected test code: "
                f"{TEST_CODE}"
            )

        print()
        print(
            "Reading conversation history"
        )
        print("-" * 68)

        history = await service.history(
            session.public_id
        )

        if not history:
            raise RuntimeError(
                "Conversation history was empty."
            )

        print(
            "History payload keys: "
            + ", ".join(
                str(key)
                for key in history.keys()
            )
        )

        current = await service.get_session(
            session.public_id
        )

        print(
            f"Session busy: "
            f"{current.is_busy}"
        )

        print()
        print(
            "Deleting managed session"
        )
        print("-" * 68)

        await service.delete_session(
            session.public_id,
            delete_workspace=True,
        )

        public_id = None

        remaining = await service.list_sessions()

        if remaining:
            raise RuntimeError(
                "The session registry was "
                "not empty after deletion."
            )

        print(
            "Session deleted successfully."
        )

        await service.close()
        service = None

        gc.collect()
        await asyncio.sleep(0.2)

        test_completed = True

    finally:
        if service is not None:
            if public_id is not None:
                with suppress(Exception):
                    await service.delete_session(
                        public_id,
                        delete_workspace=True,
                    )

            with suppress(Exception):
                await service.close()

        service = None
        public_id = None

        gc.collect()
        await asyncio.sleep(0.2)

        cleanup_succeeded = (
            await cleanup_directory_with_retries(
                temporary_root
            )
        )

    if not test_completed:
        raise RuntimeError(
            "The AgentService smoke test "
            "did not complete."
        )

    print()
    print("=" * 68)
    print(
        "AgentService smoke test "
        "completed successfully"
    )
    print("=" * 68)

    if not cleanup_succeeded:
        print(
            "The AgentService test passed. "
            "Only Windows temporary-directory "
            "cleanup was deferred."
        )


if __name__ == "__main__":
    asyncio.run(main())