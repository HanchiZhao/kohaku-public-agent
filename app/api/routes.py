from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.agent_service import (
    AgentService,
    AgentServiceError,
    SessionBusyError,
)
from app.api.dependencies import get_agent_service
from app.api.schemas import (
    CreateSessionRequest,
    HistoryResponse,
    InterruptResponse,
    MessageRequest,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["agent"],
)


# ---------------------------------------------------------------------------
# Output protocol filtering
# ---------------------------------------------------------------------------

_ZERO_WIDTH_PATTERN = re.compile(
    r"[\u200B-\u200D\u2060\uFEFF]"
)

_OUTPUT_MARKER_PATTERNS = (
    # Examples:
    # [output_final]
    # [/output_final]
    # [output_final/]
    # \[/output_final\]
    re.compile(
        r"""
        \\?
        \[
        \s*
        /?
        \s*
        output
        (?:
            [\s_-]*
            (?:final|text)
        )?
        \s*
        /?
        \s*
        \\?
        \]
        """,
        re.IGNORECASE | re.VERBOSE,
    ),

    # Examples:
    # <output_final>
    # </output_final>
    # <output_final/>
    re.compile(
        r"""
        <
        \s*
        /?
        \s*
        output
        (?:
            [\s_-]*
            (?:final|text)
        )?
        \s*
        /?
        \s*
        >
        """,
        re.IGNORECASE | re.VERBOSE,
    ),

    # Examples:
    # {{output_final}}
    # {{/output_final}}
    re.compile(
        r"""
        \{\{
        \s*
        /?
        \s*
        output
        (?:
            [\s_-]*
            (?:final|text)
        )?
        \s*
        /?
        \s*
        \}\}
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
)

_STANDALONE_OUTPUT_LINE_PATTERN = re.compile(
    r"""
    (?im)
    ^
    [\s`*_~-]*
    /?
    \s*
    output
    [\s_-]*
    (?:final|text)
    \s*
    /?
    [\s`*_~-]*
    $
    """,
    re.VERBOSE,
)


def _strip_output_protocol(text: str) -> str:
    """Remove internal output protocol markers from model text."""

    cleaned = _ZERO_WIDTH_PATTERN.sub(
        "",
        str(text),
    )

    previous = None

    while previous != cleaned:
        previous = cleaned

        for pattern in _OUTPUT_MARKER_PATTERNS:
            cleaned = pattern.sub("", cleaned)

        cleaned = _STANDALONE_OUTPUT_LINE_PATTERN.sub(
            "",
            cleaned,
        )

    return cleaned


def _clean_assistant_text(
    text: str,
    *,
    trim_edges: bool = True,
) -> str:
    """Return user-visible assistant text."""

    cleaned = _strip_output_protocol(text)

    if trim_edges:
        cleaned = cleaned.strip()

    return cleaned


def _partial_marker_suffix_length(text: str) -> int:
    """Return the length of a possible split output marker.

    An SSE network chunk may end with text such as ``[/out`` and the
    remainder may arrive in the next chunk. Holding that suffix prevents
    the browser from briefly displaying an incomplete protocol marker.
    """

    source = str(text)

    if not source:
        return 0

    scan_start = max(0, len(source) - 96)

    opening_positions = [
        source.rfind("[", scan_start),
        source.rfind("\\[", scan_start),
        source.rfind("<", scan_start),
        source.rfind("{{", scan_start),
    ]

    opening_positions = [
        position
        for position in opening_positions
        if position >= scan_start
    ]

    if not opening_positions:
        return 0

    opening = max(opening_positions)

    if (
        source[opening] == "["
        and opening > 0
        and source[opening - 1] == "\\"
    ):
        opening -= 1

    suffix = source[opening:]

    # A complete marker should already have been removed by
    # _strip_output_protocol(). If it still has a closing character,
    # it is more likely normal user-visible content.
    if (
        suffix.endswith("]")
        or suffix.endswith("\\]")
        or suffix.endswith(">")
        or suffix.endswith("}}")
    ):
        return 0

    probe = suffix.lower()

    probe = re.sub(
        r"^(?:\\?\[|<|\{\{)",
        "",
        probe,
    )

    probe = re.sub(
        r"^\s*/?\s*",
        "",
        probe,
    )

    probe = re.sub(
        r"[\s_\\/-]",
        "",
        probe,
    )

    possible_names = (
        "output",
        "outputfinal",
        "outputtext",
    )

    if not probe:
        return len(suffix)

    if any(
        name.startswith(probe)
        for name in possible_names
    ):
        return len(suffix)

    return 0


class _OutputProtocolFilter:
    """Stateful cleaner for output markers split across SSE chunks."""

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, value: str) -> str:
        self._pending += str(value)

        self._pending = _strip_output_protocol(
            self._pending
        )

        retained_length = _partial_marker_suffix_length(
            self._pending
        )

        safe_length = (
            len(self._pending) - retained_length
        )

        safe_text = self._pending[:safe_length]

        self._pending = self._pending[safe_length:]

        return safe_text

    def flush(self) -> str:
        remaining = _strip_output_protocol(
            self._pending
        )

        self._pending = ""

        return remaining


# ---------------------------------------------------------------------------
# History normalization and recovery
# ---------------------------------------------------------------------------

def _normalize_message_role(
    value: dict[str, Any],
) -> str | None:
    raw_role = str(
        value.get("role")
        or value.get("sender")
        or value.get("type")
        or value.get("author")
        or ""
    ).lower()

    if (
        "assistant" in raw_role
        or "agent" in raw_role
        or "model" in raw_role
    ):
        return "assistant"

    if (
        "user" in raw_role
        or "human" in raw_role
    ):
        return "user"

    return None


def _extract_text_content(
    value: Any,
    *,
    depth: int = 0,
) -> str:
    """Extract readable text from common history message structures."""

    if depth > 8 or value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "".join(
            _extract_text_content(
                item,
                depth=depth + 1,
            )
            for item in value
        )

    if isinstance(value, dict):
        candidate_keys = (
            "content",
            "text",
            "message",
            "value",
            "output_text",
            "output",
        )

        for key in candidate_keys:
            if key not in value:
                continue

            extracted = _extract_text_content(
                value[key],
                depth=depth + 1,
            )

            if extracted:
                return extracted

    return ""


def _collect_assistant_messages(
    value: Any,
    destination: list[dict[str, Any]],
    *,
    depth: int = 0,
) -> None:
    """Collect assistant message objects from a nested history payload."""

    if depth > 10:
        return

    if isinstance(value, list):
        for item in value:
            _collect_assistant_messages(
                item,
                destination,
                depth=depth + 1,
            )

        return

    if not isinstance(value, dict):
        return

    if _normalize_message_role(value) == "assistant":
        destination.append(value)

    for nested_value in value.values():
        _collect_assistant_messages(
            nested_value,
            destination,
            depth=depth + 1,
        )


def _extract_last_assistant_text(
    history: Any,
) -> str:
    """Recover the latest visible assistant response from history."""

    assistant_messages: list[dict[str, Any]] = []

    _collect_assistant_messages(
        history,
        assistant_messages,
    )

    for message in reversed(assistant_messages):
        text = _clean_assistant_text(
            _extract_text_content(message)
        )

        if text:
            return text

    return ""


def _sanitize_history_payload(
    value: Any,
    *,
    inherited_role: str | None = None,
    depth: int = 0,
) -> Any:
    """Remove output markers from assistant entries in history."""

    if depth > 12:
        return value

    if isinstance(value, list):
        return [
            _sanitize_history_payload(
                item,
                inherited_role=inherited_role,
                depth=depth + 1,
            )
            for item in value
        ]

    if isinstance(value, dict):
        current_role = (
            _normalize_message_role(value)
            or inherited_role
        )

        return {
            key: _sanitize_history_payload(
                nested_value,
                inherited_role=current_role,
                depth=depth + 1,
            )
            for key, nested_value in value.items()
        }

    if (
        isinstance(value, str)
        and inherited_role == "assistant"
    ):
        return _clean_assistant_text(
            value,
            trim_edges=False,
        )

    return value


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------

def _to_session_response(
    session: object,
) -> SessionResponse:
    """Convert internal SessionInfo into safe public metadata."""

    to_dict = getattr(
        session,
        "to_dict",
        None,
    )

    if not callable(to_dict):
        raise TypeError(
            "Session object does not provide to_dict()."
        )

    return SessionResponse.model_validate(
        to_dict()
    )


def _encode_sse(
    event: str,
    data: dict[str, Any],
) -> str:
    """Encode one SSE event using a JSON data line."""

    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return (
        f"event: {event}\n"
        f"data: {payload}\n\n"
    )


# ---------------------------------------------------------------------------
# Session routes
# ---------------------------------------------------------------------------

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an Agent session",
)
async def create_session(
    payload: CreateSessionRequest,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> SessionResponse:
    session = await service.create_session(
        name=payload.name,
    )

    return _to_session_response(session)


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List active Agent sessions",
)
async def list_sessions(
    service: AgentService = Depends(
        get_agent_service
    ),
) -> SessionListResponse:
    sessions = await service.list_sessions()

    public_sessions = [
        _to_session_response(session)
        for session in sessions
    ]

    return SessionListResponse(
        sessions=public_sessions,
        total=len(public_sessions),
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get one Agent session",
)
async def get_session(
    session_id: str,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> SessionResponse:
    session = await service.get_session(
        session_id
    )

    return _to_session_response(session)


# ---------------------------------------------------------------------------
# Message routes
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageResponse,
    summary="Send a message and wait for the complete response",
)
async def send_message(
    session_id: str,
    payload: MessageRequest,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> MessageResponse:
    response_text = await service.chat(
        session_id,
        payload.content,
    )

    response_text = _clean_assistant_text(
        response_text
    )

    # Some providers finish the run without exposing normal stream text,
    # while still recording the final answer in session history.
    if not response_text:
        history = await service.history(
            session_id
        )

        response_text = _extract_last_assistant_text(
            history
        )

    return MessageResponse(
        session_id=session_id,
        response=response_text,
    )


@router.post(
    "/sessions/{session_id}/messages/stream",
    response_class=StreamingResponse,
    summary="Stream an Agent response as Server-Sent Events",
)
async def stream_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> StreamingResponse:
    # Validate before HTTP response headers are sent.
    session = await service.get_session(
        session_id
    )

    if session.is_busy:
        raise SessionBusyError(
            "Session is already generating: "
            f"{session_id}"
        )

    async def event_stream() -> AsyncIterator[str]:
        protocol_filter = _OutputProtocolFilter()

        visible_parts: list[str] = []

        yield _encode_sse(
            "start",
            {
                "session_id": session_id,
            },
        )

        try:
            async with aclosing(
                service.stream_message(
                    session_id,
                    payload.content,
                )
            ) as stream:
                async for raw_chunk in stream:
                    if await request.is_disconnected():
                        return

                    clean_chunk = protocol_filter.feed(
                        str(raw_chunk)
                    )

                    if clean_chunk:
                        visible_parts.append(clean_chunk)

                        yield _encode_sse(
                            "token",
                            {
                                "text": clean_chunk,
                            },
                        )

                    # Explicit cancellation point.
                    await asyncio.sleep(0)

            trailing_text = protocol_filter.flush()

            if trailing_text:
                visible_parts.append(trailing_text)

                yield _encode_sse(
                    "token",
                    {
                        "text": trailing_text,
                    },
                )

            visible_text = _clean_assistant_text(
                "".join(visible_parts)
            )

            # Recovery path:
            # if the provider emitted no visible text, check whether the
            # final response was nevertheless written into session history.
            if not visible_text:
                history = await service.history(
                    session_id
                )

                recovered_text = (
                    _extract_last_assistant_text(
                        history
                    )
                )

                if recovered_text:
                    visible_text = recovered_text

                    yield _encode_sse(
                        "token",
                        {
                            "text": recovered_text,
                            "recovered_from_history": True,
                        },
                    )

            if await request.is_disconnected():
                return

            if not visible_text:
                yield _encode_sse(
                    "error",
                    {
                        "type": "NoVisibleModelOutput",
                        "detail": (
                            "模型运行已经结束，但没有产生可见回答。"
                            "如果该问题只在多个会话同时生成时出现，"
                            "当前模型提供商可能无法稳定处理并发请求。"
                        ),
                    },
                )

                return

            yield _encode_sse(
                "done",
                {
                    "session_id": session_id,
                    "finish_reason": "stream_ended",
                },
            )

        except asyncio.CancelledError:
            raise

        except AgentServiceError as exception:
            yield _encode_sse(
                "error",
                {
                    "type": (
                        exception.__class__.__name__
                    ),
                    "detail": str(exception),
                },
            )

        except Exception:
            logger.exception(
                "Unexpected error while streaming "
                "session %s",
                session_id,
            )

            yield _encode_sse(
                "error",
                {
                    "type": "InternalStreamingError",
                    "detail": (
                        "The streaming response failed."
                    ),
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": (
                "no-cache, no-transform"
            ),
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/sessions/{session_id}/interrupt",
    response_model=InterruptResponse,
    summary="Interrupt the active Agent response",
)
async def interrupt_session(
    session_id: str,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> InterruptResponse:
    session = await service.get_session(
        session_id
    )

    await service.interrupt(session_id)

    return InterruptResponse(
        session_id=session_id,
        status="interrupt_requested",
        was_busy=session.is_busy,
    )


# ---------------------------------------------------------------------------
# History and deletion routes
# ---------------------------------------------------------------------------

@router.get(
    "/sessions/{session_id}/history",
    response_model=HistoryResponse,
    summary="Read conversation history",
)
async def get_history(
    session_id: str,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> HistoryResponse:
    history = await service.history(
        session_id
    )

    encoded_history = jsonable_encoder(
        history
    )

    sanitized_history = _sanitize_history_payload(
        encoded_history
    )

    if not isinstance(sanitized_history, dict):
        sanitized_history = {
            "history": sanitized_history,
        }

    return HistoryResponse(
        session_id=session_id,
        history=sanitized_history,
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an Agent session",
)
async def delete_session(
    session_id: str,
    service: AgentService = Depends(
        get_agent_service
    ),
) -> Response:
    await service.delete_session(
        session_id,
        delete_workspace=True,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )