from __future__ import annotations

import asyncio
import gc
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from app.agent_service import (
    AgentService,
    PublicSessionNotFoundError,
)


CLEANUP_ATTEMPTS = 12
CLEANUP_DELAY_SECONDS = 0.5


async def cleanup_directory_with_retries(
    directory: Path,
) -> bool:
    """Best-effort Windows-safe cleanup for the smoke-test directory.

    KohakuTerrarium may briefly retain a file handle for its history
    index after Studio shutdown. Windows rejects directory deletion
    while that handle is still open, so cleanup is retried after
    garbage collection and short delays.

    A cleanup failure must not incorrectly report that restart recovery
    itself failed. The remaining directory path is printed for manual
    inspection instead.
    """

    if not directory.exists():
        return True

    last_error: OSError | None = None

    for attempt in range(
        1,
        CLEANUP_ATTEMPTS + 1,
    ):
        gc.collect()

        try:
            await asyncio.to_thread(
                shutil.rmtree,
                directory,
            )

            print(
                "Temporary test directory "
                "removed successfully."
            )

            return True

        except FileNotFoundError:
            return True

        except OSError as error:
            last_error = error

            if attempt < CLEANUP_ATTEMPTS:
                print(
                    "Temporary directory is still "
                    "in use; retrying cleanup "
                    f"({attempt}/{CLEANUP_ATTEMPTS})..."
                )

                await asyncio.sleep(
                    CLEANUP_DELAY_SECONDS
                )

    print()
    print(
        "Warning: restart recovery passed, but "
        "Windows still holds a temporary index "
        "file open."
    )
    print(
        "Temporary directory left for later cleanup:"
    )
    print(directory)

    if last_error is not None:
        print(
            "Last cleanup error: "
            f"{last_error.__class__.__name__}: "
            f"{last_error}"
        )

    return False


async def main() -> None:
    """Verify a real KohakuTerrarium session survives restart."""

    print("=" * 68)
    print(
        "Starting real restart recovery smoke test"
    )
    print("=" * 68)

    test_code = (
        "KT-RESTART-"
        + uuid4().hex[:8].upper()
    )

    temporary_root = Path(
        tempfile.mkdtemp(
            prefix="kohaku-restart-smoke-"
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

    public_id: str | None = None
    first_service: AgentService | None = None
    second_service: AgentService | None = None

    test_succeeded = False

    try:
        print(
            f"Temporary root: {temporary_root}"
        )

        first_service = AgentService(
            project_root=temporary_root,
            workspace_root=workspace_root,
            session_root=session_root,
            database_path=database_path,
        )

        await first_service.start()

        created = (
            await first_service.create_session(
                name=(
                    "Real restart recovery "
                    "smoke test"
                )
            )
        )

        public_id = created.public_id

        print(
            f"Created public ID: "
            f"{public_id}"
        )

        print(
            f"First Studio ID: "
            f"{created.studio_session_id}"
        )

        first_response = (
            await first_service.chat(
                public_id,
                (
                    "请记住恢复测试代号 "
                    f"{test_code}。"
                    "这是服务器重启恢复测试。"
                    "请只回复：已记住"
                ),
            )
        )

        if not first_response.strip():
            raise RuntimeError(
                "The first model response "
                "was empty."
            )

        print(first_response)
        print("First turn completed.")

        await first_service.close()
        first_service = None

        # Give KohakuTerrarium a brief opportunity to flush the
        # session store and release background persistence work.
        gc.collect()
        await asyncio.sleep(0.5)

        print()
        print(
            "First AgentService closed."
        )
        print(
            "Starting second AgentService..."
        )

        second_service = AgentService(
            project_root=temporary_root,
            workspace_root=workspace_root,
            session_root=session_root,
            database_path=database_path,
        )

        await second_service.start()

        restored_sessions = (
            await second_service.list_sessions()
        )

        restored = next(
            (
                session
                for session in restored_sessions
                if session.public_id == public_id
            ),
            None,
        )

        if restored is None:
            raise RuntimeError(
                "The public session was not "
                "restored after restart."
            )

        print(
            f"Restored public ID: "
            f"{restored.public_id}"
        )

        print(
            f"Restored Studio ID: "
            f"{restored.studio_session_id}"
        )

        recovery_response = (
            await second_service.chat(
                public_id,
                (
                    "请只回复我在服务器重启前"
                    "让你记住的恢复测试代号，"
                    "不要添加任何解释。"
                ),
            )
        )

        print(
            f"Recovery response: "
            f"{recovery_response}"
        )

        if test_code not in recovery_response:
            raise RuntimeError(
                "The recovered conversation "
                "did not remember the test code: "
                f"{test_code}"
            )

        history = await second_service.history(
            public_id
        )

        if not history:
            raise RuntimeError(
                "Recovered history was empty."
            )

        print(
            "Recovered history verified."
        )

        await second_service.delete_session(
            public_id,
            delete_workspace=True,
        )

        public_id = None

        await second_service.close()
        second_service = None

        # Allow the deleted SessionStore and history index to finish
        # releasing their Windows file handles.
        gc.collect()
        await asyncio.sleep(1.0)

        test_succeeded = True

    finally:
        if first_service is not None:
            with suppress(Exception):
                await first_service.close()

        if second_service is not None:
            if public_id is not None:
                with suppress(
                    PublicSessionNotFoundError
                ):
                    await (
                        second_service.delete_session(
                            public_id,
                            delete_workspace=True,
                        )
                    )

            with suppress(Exception):
                await second_service.close()

        first_service = None
        second_service = None

        gc.collect()
        await asyncio.sleep(1.0)

        cleanup_succeeded = (
            await cleanup_directory_with_retries(
                temporary_root
            )
        )

    if not test_succeeded:
        raise RuntimeError(
            "The restart recovery smoke test "
            "did not complete."
        )

    print()
    print("=" * 68)
    print(
        "Real restart recovery smoke test "
        "completed successfully"
    )
    print("=" * 68)

    if not cleanup_succeeded:
        print(
            "The recovery test passed. Only the "
            "temporary Windows cleanup was deferred."
        )


if __name__ == "__main__":
    asyncio.run(main())