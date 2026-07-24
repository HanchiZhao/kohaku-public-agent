from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_INTERRUPT_ATTEMPTS = 5
NORMAL_TIMEOUT = 90.0
CONNECT_TIMEOUT = 15.0
BUSY_TIMEOUT = 10.0
IDLE_TIMEOUT = 15.0
ABORT_TIMEOUT = 5.0
CONTROL_TIMEOUT = 15.0
POLL_INTERVAL = 0.02


class InconclusiveInterruptAttempt(RuntimeError):
    """The attempt ended without proving a real active interruption."""


@dataclass(slots=True)
class Settings:
    base_url: str
    interrupt_attempts: int


@dataclass(slots=True)
class Capture:
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    start_received: bool = False
    token_count: int = 0
    chunks: list[str] = field(default_factory=list)
    done_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def parse_arguments() -> Settings:
    parser = argparse.ArgumentParser(
        description=(
            "Validate normal SSE transport and the browser-equivalent "
            "live stop flow."
        )
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="FastAPI base URL. Default: %(default)s",
    )
    parser.add_argument(
        "--interrupt-attempts",
        type=int,
        default=DEFAULT_INTERRUPT_ATTEMPTS,
        help="Maximum fresh-session live stop attempts. Default: %(default)s",
    )
    args = parser.parse_args()

    if not 1 <= args.interrupt_attempts <= 10:
        parser.error("--interrupt-attempts must be between 1 and 10")

    return Settings(
        base_url=str(args.base_url).rstrip("/"),
        interrupt_attempts=int(args.interrupt_attempts),
    )


def require_json_object(
    response: httpx.Response,
    label: str,
) -> dict[str, Any]:
    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{label} was not a JSON object: {payload!r}"
        )

    return dict(payload)


async def create_session(
    client: httpx.AsyncClient,
    base_url: str,
    name: str,
) -> str:
    response = await client.post(
        f"{base_url}/api/v1/sessions",
        json={"name": name},
    )
    response.raise_for_status()

    payload = require_json_object(
        response,
        "Create-session response",
    )

    session_id = payload.get("session_id")

    if not session_id:
        raise RuntimeError(
            "Create-session response did not contain a valid session_id: "
            f"{payload!r}"
        )

    return str(session_id)


async def delete_session(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> None:
    response = await client.delete(
        f"{base_url}/api/v1/sessions/{session_id}"
    )

    if response.status_code == 404:
        print(
            "Temporary session was already deleted: "
            f"{session_id}"
        )
        return

    if response.status_code != 204:
        raise RuntimeError(
            "Failed to delete temporary session "
            f"{session_id}: "
            f"{response.status_code} "
            f"{response.text}"
        )


async def get_status(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{base_url}/api/v1/sessions/{session_id}"
    )
    response.raise_for_status()

    payload = require_json_object(
        response,
        "Session-status response",
    )

    if not isinstance(
        payload.get("is_busy"),
        bool,
    ):
        raise RuntimeError(
            "Session-status response did not contain boolean is_busy: "
            f"{payload!r}"
        )

    return payload


async def interrupt(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{base_url}/api/v1/sessions/{session_id}/interrupt"
    )
    response.raise_for_status()

    payload = require_json_object(
        response,
        "Interrupt response",
    )

    if payload.get("session_id") != session_id:
        raise RuntimeError(
            "Interrupt returned the wrong session: "
            f"{payload!r}"
        )

    if payload.get("status") != "interrupt_requested":
        raise RuntimeError(
            "Unexpected interrupt status: "
            f"{payload!r}"
        )

    if not isinstance(
        payload.get("was_busy"),
        bool,
    ):
        raise RuntimeError(
            "Interrupt omitted boolean was_busy: "
            f"{payload!r}"
        )

    return payload


def decode_sse_data(
    raw_data: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"SSE returned invalid JSON: {raw_data}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeError(
            "SSE payload was not an object: "
            f"{payload!r}"
        )

    return payload


async def iter_sse(
    response: httpx.Response,
) -> AsyncIterator[
    tuple[str, dict[str, Any]]
]:
    current_event = ""

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

        if line.startswith("data:"):
            payload = decode_sse_data(
                line.split(
                    ":",
                    maxsplit=1,
                )[1].strip()
            )

            yield current_event, payload


def validate_sse_response(
    response: httpx.Response,
) -> None:
    response.raise_for_status()

    content_type = response.headers.get(
        "content-type",
        "",
    )

    if not content_type.startswith(
        "text/event-stream"
    ):
        raise RuntimeError(
            "Streaming endpoint did not return text/event-stream. "
            f"Received: {content_type}"
        )


async def consume_normal_stream(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> Capture:
    capture = Capture()

    async with client.stream(
        "POST",
        (
            f"{base_url}/api/v1/sessions/"
            f"{session_id}/messages/stream"
        ),
        json={
            "content": (
                "请只回复下面这句话，不要添加解释："
                "SSE 正常流式测试成功"
            )
        },
    ) as response:
        validate_sse_response(response)
        capture.connected.set()

        async for event, payload in iter_sse(
            response
        ):
            if event == "start":
                capture.start_received = True

            elif event == "token":
                text = str(
                    payload.get(
                        "text",
                        "",
                    )
                )

                if text:
                    print(
                        text,
                        end="",
                        flush=True,
                    )

                    capture.chunks.append(text)
                    capture.token_count += 1

            elif event == "done":
                capture.done_payload = payload
                break

            elif event == "error":
                capture.error_payload = payload
                break

    return capture


async def hold_live_stream(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    prompt: str,
    capture: Capture,
) -> None:
    async with client.stream(
        "POST",
        (
            f"{base_url}/api/v1/sessions/"
            f"{session_id}/messages/stream"
        ),
        json={
            "content": prompt,
        },
    ) as response:
        validate_sse_response(response)
        capture.connected.set()

        async for event, payload in iter_sse(
            response
        ):
            if event == "start":
                capture.start_received = True

            elif event == "token":
                text = str(
                    payload.get(
                        "text",
                        "",
                    )
                )

                if text:
                    print(
                        text,
                        end="",
                        flush=True,
                    )

                    capture.chunks.append(text)
                    capture.token_count += 1

            elif event == "done":
                capture.done_payload = payload
                break

            elif event == "error":
                capture.error_payload = payload
                break


async def raise_task_failure(
    task: asyncio.Task[None],
) -> None:
    if task.cancelled():
        return

    error = task.exception()

    if error is not None:
        raise RuntimeError(
            "The live SSE reader failed."
        ) from error


async def abort_stream(
    task: asyncio.Task[None],
) -> bool:
    """Return True only when this function cancels the stream."""

    if task.done():
        await raise_task_failure(task)
        return False

    task.cancel()

    try:
        await asyncio.wait_for(
            task,
            timeout=ABORT_TIMEOUT,
        )
    except asyncio.CancelledError:
        return True

    except TimeoutError as error:
        raise RuntimeError(
            "The SSE client did not close within "
            f"{ABORT_TIMEOUT:.0f} seconds."
        ) from error

    return True


async def wait_connected(
    capture: Capture,
    task: asyncio.Task[None],
) -> None:
    try:
        await asyncio.wait_for(
            capture.connected.wait(),
            timeout=CONNECT_TIMEOUT,
        )
    except TimeoutError as error:
        if task.done():
            await raise_task_failure(task)

        raise RuntimeError(
            "The live request did not establish an SSE response within "
            f"{CONNECT_TIMEOUT:.0f} seconds."
        ) from error


async def wait_busy(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    task: asyncio.Task[None],
    capture: Capture,
) -> tuple[
    dict[str, Any],
    int,
]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + BUSY_TIMEOUT

    last_payload: dict[str, Any] | None = None
    polls = 0

    while loop.time() < deadline:
        if capture.error_payload is not None:
            raise RuntimeError(
                "SSE returned an error before interruption: "
                + json.dumps(
                    capture.error_payload,
                    ensure_ascii=False,
                )
            )

        if task.done():
            await raise_task_failure(task)

            raise InconclusiveInterruptAttempt(
                "The SSE response completed before "
                "is_busy=true was observed."
            )

        last_payload = await get_status(
            client,
            base_url,
            session_id,
        )

        polls += 1

        if last_payload["is_busy"]:
            return last_payload, polls

        await asyncio.sleep(
            POLL_INTERVAL
        )

    raise RuntimeError(
        "The SSE connection opened, but the session never became busy "
        f"within {BUSY_TIMEOUT:.0f} seconds. "
        f"Last payload: {last_payload!r}"
    )


async def wait_idle(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + IDLE_TIMEOUT

    last_payload: dict[str, Any] | None = None

    while loop.time() < deadline:
        last_payload = await get_status(
            client,
            base_url,
            session_id,
        )

        if not last_payload["is_busy"]:
            return last_payload

        await asyncio.sleep(0.1)

    raise RuntimeError(
        "The interrupted session did not return to is_busy=false within "
        f"{IDLE_TIMEOUT:.0f} seconds. "
        f"Last payload: {last_payload!r}"
    )


async def run_normal_test(
    stream_client: httpx.AsyncClient,
    control_client: httpx.AsyncClient,
    base_url: str,
) -> None:
    session_id = await create_session(
        control_client,
        base_url,
        "Normal SSE smoke test",
    )

    print()
    print("=" * 68)
    print("Test 1 - normal SSE token streaming")
    print("=" * 68)
    print(f"Session ID: {session_id}")
    print(
        "Assistant: ",
        end="",
        flush=True,
    )

    try:
        try:
            result = await asyncio.wait_for(
                consume_normal_stream(
                    stream_client,
                    base_url,
                    session_id,
                ),
                timeout=NORMAL_TIMEOUT,
            )
        except TimeoutError as error:
            raise RuntimeError(
                "Normal SSE did not finish within "
                f"{NORMAL_TIMEOUT:.0f} seconds."
            ) from error

        print()

        if not result.start_received:
            raise RuntimeError(
                "Normal SSE did not return a start event."
            )

        if (
            result.token_count == 0
            or not result.text.strip()
        ):
            raise RuntimeError(
                "Normal SSE returned no visible token text."
            )

        if result.error_payload is not None:
            raise RuntimeError(
                "Normal SSE returned an error event: "
                + json.dumps(
                    result.error_payload,
                    ensure_ascii=False,
                )
            )

        if result.done_payload is None:
            raise RuntimeError(
                "Normal SSE did not return a done event."
            )

        if (
            result.done_payload.get(
                "finish_reason"
            )
            != "stream_ended"
        ):
            raise RuntimeError(
                "Unexpected done payload: "
                f"{result.done_payload!r}"
            )

        print(
            "Token events received: "
            f"{result.token_count}"
        )
        print(
            f"Visible text: {result.text!r}"
        )
        print(
            f"Done payload: {result.done_payload}"
        )
        print(
            "Normal SSE streaming test passed."
        )

    finally:
        await delete_session(
            control_client,
            base_url,
            session_id,
        )


def build_long_prompt(
    attempt: int,
) -> str:
    upper_bound = (
        2000
        + (attempt - 1) * 500
    )

    return (
        f"请从 1 开始逐行输出到 {upper_bound}。"
        "每行只输出一个阿拉伯数字，"
        "不要省略、总结、使用代码块或提前结束。"
    )


async def run_one_stop_attempt(
    control_client: httpx.AsyncClient,
    base_url: str,
    attempt: int,
    maximum: int,
) -> None:
    session_id = await create_session(
        control_client,
        base_url,
        f"SSE interruption smoke test {attempt}",
    )

    capture = Capture()

    task: asyncio.Task[None] | None = None

    print()
    print("-" * 68)
    print(
        f"Live stop attempt {attempt}/{maximum}"
    )
    print(
        f"Session ID: {session_id}"
    )
    print(
        "Interrupting as soon as the server reports is_busy=true."
    )
    print("-" * 68)

    try:
        stream_timeout = httpx.Timeout(
            connect=15.0,
            read=None,
            write=30.0,
            pool=15.0,
        )

        async with httpx.AsyncClient(
            timeout=stream_timeout
        ) as stream_client:
            task = asyncio.create_task(
                hold_live_stream(
                    stream_client,
                    base_url,
                    session_id,
                    build_long_prompt(
                        attempt
                    ),
                    capture,
                )
            )

            await wait_connected(
                capture,
                task,
            )

            busy_payload, polls = await wait_busy(
                control_client,
                base_url,
                session_id,
                task,
                capture,
            )

            print()
            print(
                "[CONTROL] is_busy=true observed "
                f"after {polls} poll(s):"
            )
            print(
                json.dumps(
                    busy_payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            try:
                interrupt_payload = await interrupt(
                    control_client,
                    base_url,
                    session_id,
                )
            finally:
                client_aborted = await abort_stream(
                    task
                )

            print()
            print(
                "[CONTROL] Interrupt response:"
            )
            print(
                json.dumps(
                    interrupt_payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            if not interrupt_payload["was_busy"]:
                final_state = await wait_idle(
                    control_client,
                    base_url,
                    session_id,
                )

                raise InconclusiveInterruptAttempt(
                    "The model finished between busy observation "
                    "and the interrupt snapshot. "
                    f"Final state: {final_state!r}"
                )

            if not client_aborted:
                if capture.error_payload is not None:
                    raise RuntimeError(
                        "The stream returned an error before client abort: "
                        + json.dumps(
                            capture.error_payload,
                            ensure_ascii=False,
                        )
                    )

                raise InconclusiveInterruptAttempt(
                    "was_busy=true was returned, but the stream had "
                    "already ended before the browser-equivalent "
                    "client abort."
                )

            final_state = await wait_idle(
                control_client,
                base_url,
                session_id,
            )

            print()
            print(
                "SSE start received: "
                f"{capture.start_received}"
            )
            print(
                "Tokens before abort: "
                f"{capture.token_count}"
            )
            print(
                f"Partial text: {capture.text!r}"
            )
            print(
                f"Final session state: {final_state}"
            )
            print(
                "Browser-equivalent live stop attempt passed."
            )

    finally:
        if (
            task is not None
            and not task.done()
        ):
            await abort_stream(task)

        await delete_session(
            control_client,
            base_url,
            session_id,
        )


async def run_stop_test(
    control_client: httpx.AsyncClient,
    base_url: str,
    maximum_attempts: int,
) -> None:
    print()
    print("=" * 68)
    print(
        "Test 2 - browser-equivalent live SSE stop flow"
    )
    print("=" * 68)
    print(
        "Test 1 separately verifies visible token and done events."
    )
    print(
        "Test 2 requires SSE connection, is_busy=true, "
        "was_busy=true, client abort, and final is_busy=false."
    )

    inconclusive: list[str] = []

    for attempt in range(
        1,
        maximum_attempts + 1,
    ):
        try:
            await run_one_stop_attempt(
                control_client,
                base_url,
                attempt,
                maximum_attempts,
            )

            print(
                "SSE live stop test passed."
            )
            return

        except InconclusiveInterruptAttempt as error:
            inconclusive.append(
                str(error)
            )

            print()
            print(
                f"[INCONCLUSIVE] {error}"
            )

            if attempt < maximum_attempts:
                print(
                    "Retrying with a new temporary session..."
                )
                await asyncio.sleep(1.0)

    print()
    print(
        "Inconclusive attempt summary:"
    )

    for index, message in enumerate(
        inconclusive,
        start=1,
    ):
        print(
            f"  {index}. {message}"
        )

    raise RuntimeError(
        "Normal SSE passed, but no attempt proved the live browser "
        "stop flow. The validation is failing rather than silently passing."
    )


async def run(
    settings: Settings,
) -> None:
    stream_timeout = httpx.Timeout(
        connect=15.0,
        read=None,
        write=30.0,
        pool=15.0,
    )

    limits = httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
    )

    async with (
        httpx.AsyncClient(
            timeout=stream_timeout,
            limits=limits,
        ) as stream_client,
        httpx.AsyncClient(
            timeout=CONTROL_TIMEOUT,
            limits=limits,
        ) as control_client,
    ):
        health_response = await control_client.get(
            f"{settings.base_url}/health"
        )
        health_response.raise_for_status()

        health = require_json_object(
            health_response,
            "Health response",
        )

        print(
            f"Health: {health}"
        )

        if health.get("status") != "ok":
            raise RuntimeError(
                "The API health endpoint is not healthy."
            )

        await run_normal_test(
            stream_client,
            control_client,
            settings.base_url,
        )

        await run_stop_test(
            control_client,
            settings.base_url,
            settings.interrupt_attempts,
        )

    print()
    print("=" * 68)
    print(
        "All SSE smoke tests completed successfully"
    )
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(
        run(
            parse_arguments()
        )
    )