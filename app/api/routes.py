from __future__ import annotations

import asyncio
import json
import logging
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


def _to_session_response(
    session: object,
) -> SessionResponse:
    """Convert internal SessionInfo into safe public metadata."""
    to_dict = getattr(session, "to_dict", None)

    if not callable(to_dict):
        raise TypeError(
            "Session object does not provide to_dict()."
        )

    return SessionResponse.model_validate(to_dict())


def _encode_sse(
    event: str,
    data: dict[str, Any],
) -> str:
    """Encode one SSE event using a single JSON data line."""
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return f"event: {event}\ndata: {payload}\n\n"


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an Agent session",
)
async def create_session(
    payload: CreateSessionRequest,
    service: AgentService = Depends(get_agent_service),
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
    service: AgentService = Depends(get_agent_service),
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
    service: AgentService = Depends(get_agent_service),
) -> SessionResponse:
    session = await service.get_session(session_id)

    return _to_session_response(session)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageResponse,
    summary="Send a message and wait for the complete response",
)
async def send_message(
    session_id: str,
    payload: MessageRequest,
    service: AgentService = Depends(get_agent_service),
) -> MessageResponse:
    response_text = await service.chat(
        session_id,
        payload.content,
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
    service: AgentService = Depends(get_agent_service),
) -> StreamingResponse:
    # Validate the session before response headers are sent.
    session = await service.get_session(session_id)

    if session.is_busy:
        raise SessionBusyError(
            f"Session is already generating: {session_id}"
        )

    async def event_stream() -> AsyncIterator[str]:
        yield _encode_sse(
            "start",
            {"session_id": session_id},
        )

        try:
            # aclosing guarantees that an unfinished Agent stream is
            # closed if the browser disconnects or this response stops.
            async with aclosing(
                service.stream_message(
                    session_id,
                    payload.content,
                )
            ) as stream:
                async for chunk in stream:
                    if await request.is_disconnected():
                        return

                    yield _encode_sse(
                        "token",
                        {"text": chunk},
                    )

                    # Explicit cancellation point for disconnect handling.
                    await asyncio.sleep(0)

            if not await request.is_disconnected():
                yield _encode_sse(
                    "done",
                    {
                        "session_id": session_id,
                        "finish_reason": "stream_ended",
                    },
                )

        except asyncio.CancelledError:
            # Let FastAPI/Starlette cancel the response. The aclosing
            # context will close AgentService.stream_message(), whose
            # cleanup path interrupts the underlying Agent generation.
            raise

        except AgentServiceError as exception:
            # HTTP headers have already been sent, so errors during an
            # active stream must be represented as SSE events.
            yield _encode_sse(
                "error",
                {
                    "type": exception.__class__.__name__,
                    "detail": str(exception),
                },
            )

        except Exception:
            logger.exception(
                "Unexpected error while streaming session %s",
                session_id,
            )

            yield _encode_sse(
                "error",
                {
                    "type": "InternalStreamingError",
                    "detail": "The streaming response failed.",
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
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
    service: AgentService = Depends(get_agent_service),
) -> InterruptResponse:
    session = await service.get_session(session_id)

    await service.interrupt(session_id)

    return InterruptResponse(
        session_id=session_id,
        status="interrupt_requested",
        was_busy=session.is_busy,
    )


@router.get(
    "/sessions/{session_id}/history",
    response_model=HistoryResponse,
    summary="Read conversation history",
)
async def get_history(
    session_id: str,
    service: AgentService = Depends(get_agent_service),
) -> HistoryResponse:
    history = await service.history(session_id)

    return HistoryResponse(
        session_id=session_id,
        history=jsonable_encoder(history),
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an Agent session",
)
async def delete_session(
    session_id: str,
    service: AgentService = Depends(get_agent_service),
) -> Response:
    await service.delete_session(
        session_id,
        delete_workspace=True,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )