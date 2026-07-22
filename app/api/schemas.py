from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    """Base schema that rejects unexpected request fields."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class CreateSessionRequest(StrictSchema):
    """Request body for creating an Agent session."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Optional user-facing conversation name.",
    )


class SessionResponse(StrictSchema):
    """Public metadata for one Agent session."""

    session_id: str
    name: str
    created_at: datetime
    is_busy: bool


class SessionListResponse(StrictSchema):
    """List of all sessions held by this service process."""

    sessions: list[SessionResponse]
    total: int


class MessageRequest(StrictSchema):
    """A user message sent to an existing Agent session."""

    content: str = Field(
        min_length=1,
        max_length=50_000,
        description="The text submitted by the user.",
    )


class MessageResponse(StrictSchema):
    """Complete, non-streaming Agent response."""

    session_id: str
    response: str


class InterruptResponse(StrictSchema):
    """Result of requesting an interruption."""

    session_id: str
    status: Literal["interrupt_requested"]
    was_busy: bool


class HistoryResponse(StrictSchema):
    """Conversation history returned by KohakuTerrarium."""

    session_id: str
    history: dict[str, Any]


class HealthResponse(StrictSchema):
    """Application health status."""

    status: str
    agent_service_started: bool