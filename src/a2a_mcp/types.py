"""Pydantic models for the call_agent tool I/O contract.

These models shape the public API of a2a-mcp. The underlying A2A types are
protobuf-based and exposed to MCP clients only via the JSON shape produced
from these Pydantic models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ArtifactPart(BaseModel):
    """A single part within an artifact (text / file / data)."""

    text: str | None = None
    url: str | None = None
    data: Any | None = None
    filename: str | None = None
    media_type: str | None = None


class ArtifactSummary(BaseModel):
    """A normalized artifact returned to the MCP caller."""

    artifact_id: str
    name: str = ""
    description: str = ""
    parts: list[ArtifactPart] = Field(default_factory=list)


class CallAgentInput(BaseModel):
    """Input contract for the call_agent MCP tool.

    Core fields only. Additional A2A features (history_length, blocking,
    push_notifications, accepted_output_modes) can be added in later
    iterations without breaking existing callers.
    """

    agent_url: HttpUrl = Field(
        ..., description="Root URL of the target A2A agent (must be http/https)."
    )
    text: str = Field(..., min_length=1, description="User message text.")
    context_id: str | None = Field(
        default=None,
        description="Optional multi-turn dialog context ID; auto-generated if omitted.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional free-form metadata forwarded to the agent."
    )

    @field_validator("agent_url", mode="before")
    @classmethod
    def _coerce_url(cls, v: Any) -> Any:
        # Allow plain string URLs; HttpUrl will validate scheme/format.
        return v


class CallAgentResult(BaseModel):
    """Result returned to MCP clients from a successful call_agent invocation."""

    task_id: str = ""
    context_id: str = ""
    state: str = Field(..., description="A2A task final state, e.g. TASK_STATE_COMPLETED.")
    agent_response: str = Field(
        default="",
        description="Concatenated agent text reply, suitable for direct LLM consumption.",
    )
    artifacts: list[ArtifactSummary] = Field(default_factory=list)