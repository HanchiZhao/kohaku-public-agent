from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:8000"


@dataclass(slots=True)
class StreamResult:
    """Collected result from one SSE response."""

    token_count: int
    text: str
    done_received: bool
    done_payload: dict[str, Any] | None


async def create_session(
    client: httpx.AsyncClient,
    name: str,
) -> str:
    """Create one temporary Agent session."""
    response = await client.post(
        f"{BASE_URL}/api/v1/sessions",
        json={"name": name},
    )
    response.raise_for_status()

    return str(response.json()["session_id"])


async def delete_session(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    """Delete a temporary Agent session."""
    response = await client.delete(
        f"{BASE_URL}/api/v1/sessions/{session_id}"
    )

    if response.status_code != 204:
        raise RuntimeError(
            "Failed to delete temporary session "
            f"{session_id}: {response.status_code} {response.text}"
        )


async def consume_sse(
    client: httpx.AsyncClient,
    session_id: str,
    content: str,
) -> StreamResult:
    """Read one complete SSE response and print token events."""
    current_event = ""
    token_count = 0
    text_chunks: list[str] = []
    done_received = False
    done_payload: dict[str, Any] | None = None

    async with client.stream(
        "POST",
        (
            f"{BASE_URL}/api/v1/sessions/"
            f"{session_id}/messages/stream"
        ),
        json={"content": content},
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        )

        if not content_type.startswith("text/event-stream"):
            raise RuntimeError(
                "Streaming endpoint did not return text/event-stream. "
                f"Received: {content_type}"
            )

        async for line in response.aiter_lines():
            if not line:
                current_event = ""
                continue

            if line.startswith("event:"):
                current_event = line.split(
                    ":",
                    maxsplit=1,
                )[1].strip()
                continue

            if not line.startswith("data:"):
                continue

            raw_data = line.split(
                ":",
                maxsplit=1,
            )[1].strip()

            payload = json.loads(raw_data)

            if current_event == "token":
                text = str(payload.get("text", ""))

                if text:
                    print(text, end="", flush=True)
                    text_chunks.append(text)
                    token_count += 1

            elif current_event == "done":
                done_received = True
                done_payload = payload

            elif current_event == "error":
                raise RuntimeError(
                    f"SSE error event received: {payload}"
                )

    return StreamResult(
        token_count=token_count,
        text="".join(text_chunks),
        done_received=done_received,
        done_payload=done_payload,
    )


async def interrupt_when_busy(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Wait until the session is generating, then interrupt it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        status_response = await client.get(
            f"{BASE_URL}/api/v1/sessions/{session_id}"
        )
        status_response.raise_for_status()

        if bool(status_response.json()["is_busy"]):
            interrupt_response = await client.post(
                (
                    f"{BASE_URL}/api/v1/sessions/"
                    f"{session_id}/interrupt"
                )
            )
            interrupt_response.raise_for_status()

            return dict(interrupt_response.json())

        await asyncio.sleep(0.1)

    raise RuntimeError(
        "The session did not enter the busy state before timeout."
    )


async def run_normal_stream_test(
    client: httpx.AsyncClient,
) -> None:
    """Verify that normal SSE streaming produces text tokens."""
    session_id = await create_session(
        client,
        "Normal SSE smoke test",
    )

    print()
    print("=" * 60)
    print("Test 1 — normal SSE token streaming")
    print("=" * 60)
    print(f"Session ID: {session_id}")
    print()
    print("Assistant: ", end="", flush=True)

    try:
        result = await consume_sse(
            client,
            session_id,
            "请只回复这一句话：SSE 正常流式测试成功",
        )

        print()
        print()

        if result.token_count == 0:
            raise RuntimeError(
                "Normal streaming returned no token events."
            )

        if not result.text.strip():
            raise RuntimeError(
                "Normal streaming returned empty text."
            )

        if not result.done_received:
            raise RuntimeError(
                "Normal streaming did not return a done event."
            )

        print(f"Token events received: {result.token_count}")
        print(f"Done payload: {result.done_payload}")
        print("Normal SSE streaming test passed.")

    finally:
        await delete_session(client, session_id)


async def run_interrupt_test(
    client: httpx.AsyncClient,
) -> None:
    """Verify that an active stream can be interrupted."""
    session_id = await create_session(
        client,
        "SSE interruption smoke test",
    )

    print()
    print("=" * 60)
    print("Test 2 — interruption of an active SSE response")
    print("=" * 60)
    print(f"Session ID: {session_id}")
    print()
    print("Assistant output before interruption: ")
    print("-" * 60)

    interrupt_task = asyncio.create_task(
        interrupt_when_busy(
            client,
            session_id,
        )
    )

    try:
        stream_result = await consume_sse(
            client,
            session_id,
            (
                "请详细介绍人工智能的发展历史、主要技术路线、"
                "典型应用、社会影响与未来挑战，写成一篇很长的文章。"
            ),
        )

        interrupt_payload = await interrupt_task

        print()
        print("-" * 60)
        print(f"Interrupt response: {interrupt_payload}")
        print(
            "Token events received before interruption: "
            f"{stream_result.token_count}"
        )
        print(
            "Done event received: "
            f"{stream_result.done_received}"
        )
        print(f"Done payload: {stream_result.done_payload}")

        if not bool(interrupt_payload.get("was_busy")):
            raise RuntimeError(
                "Interrupt endpoint did not observe an active generation."
            )

        # It is valid to receive zero tokens here. The interruption may
        # happen during provider startup, before the first model token.
        print("SSE interruption test passed.")

    finally:
        if not interrupt_task.done():
            interrupt_task.cancel()

            try:
                await interrupt_task
            except asyncio.CancelledError:
                pass

        await delete_session(client, session_id)


async def main() -> None:
    """Run independent streaming and interruption tests."""
    async with httpx.AsyncClient(timeout=None) as client:
        health_response = await client.get(
            f"{BASE_URL}/health"
        )
        health_response.raise_for_status()

        health_payload = health_response.json()

        print(f"Health: {health_payload}")

        if health_payload.get("status") != "ok":
            raise RuntimeError(
                "The API health endpoint is not healthy."
            )

        await run_normal_stream_test(client)
        await run_interrupt_test(client)

    print()
    print("=" * 60)
    print("All SSE smoke tests completed successfully")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())