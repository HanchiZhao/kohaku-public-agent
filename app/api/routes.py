from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Response,
    status,
)
from fastapi.encoders import jsonable_encoder

from app.agent_service import AgentService
from app.api.dependencies import get_agent_service
from app.api.schemas import (
    CreateSessionRequest,
    HistoryResponse,
    MessageRequest,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)


router = APIRouter(
    prefix="/api/v1",
    tags=["agent"],
)


def _to_session_response(
    session: object,
) -> SessionResponse:
    """Convert an internal SessionInfo into safe public metadata."""

    to_dict = getattr(session, "to_dict", None)

    if not callable(to_dict):
        raise TypeError(
            "Session object does not provide to_dict()."
        )

    return SessionResponse.model_validate(to_dict())


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