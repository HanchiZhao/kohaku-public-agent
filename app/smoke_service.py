from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.agent_service import AgentService


TEST_CODE = "KOHAKU-SERVICE-2026"


async def collect_stream(
    stream: AsyncIterator[str],
) -> str:
    """Collect and display a streaming response."""
    chunks: list[str] = []

    async for chunk in stream:
        print(chunk, end="", flush=True)
        chunks.append(chunk)

    print()
    return "".join(chunks)


async def main() -> None:
    print("=" * 60)
    print("Starting long-running AgentService smoke test")
    print("=" * 60)

    async with AgentService() as service:
        print(f"Service started: {service.is_started}")

        session = await service.create_session(
            name="agent-service-smoke-test"
        )

        print(f"Public session ID: {session.public_id}")
        print(
            f"Studio session ID: "
            f"{session.studio_session_id}"
        )
        print(f"Creature ID: {session.creature_id}")
        print(f"Workspace: {session.workspace}")

        sessions = await service.list_sessions()

        if len(sessions) != 1:
            raise RuntimeError(
                "Expected exactly one managed session."
            )

        print()
        print("Turn 1 — storing a value")
        print("-" * 60)

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
                "The first Agent response was empty."
            )

        print(response_one)

        print()
        print("Turn 2 — verifying conversation memory")
        print("-" * 60)

        response_two = await collect_stream(
            service.stream_message(
                session.public_id,
                (
                    "请只回复我上一条消息中的测试代号，"
                    "不要添加解释或其他文字。"
                ),
            )
        )

        if TEST_CODE not in response_two:
            raise RuntimeError(
                "The second response did not contain "
                f"the expected test code: {TEST_CODE}"
            )

        print()
        print("Reading conversation history")
        print("-" * 60)

        history = await service.history(
            session.public_id
        )

        if not history:
            raise RuntimeError(
                "Conversation history was empty."
            )

        print(
            "History payload keys: "
            + ", ".join(str(key) for key in history.keys())
        )

        current = await service.get_session(
            session.public_id
        )

        print(f"Session busy: {current.is_busy}")

        print()
        print("Deleting managed session")
        print("-" * 60)

        await service.delete_session(
            session.public_id,
            delete_workspace=False,
        )

        remaining = await service.list_sessions()

        if remaining:
            raise RuntimeError(
                "The session registry was not empty after deletion."
            )

        print("Session deleted successfully.")

    print()
    print("=" * 60)
    print("AgentService smoke test completed successfully")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())