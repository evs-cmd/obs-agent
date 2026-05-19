from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's observability query")
    session_id: str | None = Field(None, description="Optional session ID for memory continuity")


class AskEvent(BaseModel):
    type: str  # "route" | "token" | "tool_call" | "tool_result" | "done" | "error"
    data: dict | str | None = None
