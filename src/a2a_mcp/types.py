"""Pydantic models for the call_agent tool I/O contract.

These models shape the public API of a2a-mcp. The underlying A2A types are
protobuf-based and exposed to MCP clients only via the JSON shape produced
from these Pydantic models.

Design note: this layer is a *transport*, not a *synthesizer*. We extract
fields the agent actually emitted (artifacts, status message) and surface
them as-is — we do not fold, merge, or join channels into a synthetic
"agent_response" string. Callers that want a flat text reply should pick
the channel they care about and concatenate themselves.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, PlainSerializer, field_validator


def _b64(v: bytes | None) -> str | None:
    """Serialize ``bytes`` to a base64 string for JSON consumers.

    MCP delivers tool results as JSON, which has no native bytes — base64 is
    the standard encoding, recognized by every language's JSON library.
    """
    import base64

    if v is None:
        return None
    return base64.b64encode(v).decode("ascii")


# Annotated alias for "bytes field that serializes to base64 in JSON".
RawBytesField = Annotated[
    bytes | None,
    PlainSerializer(_b64, return_type=str | None, when_used="json"),
]


class ArtifactPart(BaseModel):
    """A single part within an artifact (text / file / data).

    Mirrors ``a2a.types.Part`` — every oneof is exposed as its own nullable
    attribute, so callers can read whichever channel the agent used. The
    protobuf ``raw`` (bytes) field is exposed as ``raw``; in JSON output it
    serializes to a base64 string so MCP consumers (which speak JSON only)
    can decode it. Pass-through: no synthesis across channels.
    """

    text: str | None = None
    raw: RawBytesField = None
    url: str | None = None
    data: Any = None
    metadata: dict[str, Any] | None = None
    filename: str | None = None
    media_type: str | None = None


class ArtifactSummary(BaseModel):
    """A normalized artifact returned to the MCP caller."""

    artifact_id: str
    name: str = ""
    description: str = ""
    parts: list[ArtifactPart] = Field(default_factory=list)


class StatusMessagePart(BaseModel):
    """A single part of an A2A message — text, structured data, or file ref.

    Mirrors ``a2a.types.Part`` but with the protobuf ``Value`` already
    coerced into plain Python primitives so the result serializes cleanly.
    The ``raw`` bytes field base64-encodes to a string in JSON output.
    """

    text: str | None = None
    raw: RawBytesField = None
    url: str | None = None
    data: Any = None
    metadata: dict[str, Any] | None = None
    filename: str | None = None
    media_type: str | None = None


class StatusMessage(BaseModel):
    """Agent's message attached to the final task status update.

    Populated only when the agent embeds a message in the status — typical
    cases:
      * ``state=TASK_STATE_INPUT_REQUIRED`` with a form schema in ``data``
      * ``state=TASK_STATE_AUTH_REQUIRED`` with an auth challenge in ``text``
      * ``state=TASK_STATE_FAILED`` / ``REJECTED`` with a reason in ``text``

    For ordinary ``TASK_STATE_COMPLETED`` replies, the agent's text usually
    lives in ``artifacts`` (or in the message-channel events we deliberately
    do not surface — see CallAgentResult's docstring).
    """

    role: str = ""
    parts: list[StatusMessagePart] = Field(default_factory=list)


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
    """Result returned to MCP clients from a successful call_agent invocation.

    Fields are an extraction of the raw A2A response — nothing is folded,
    merged, or synthesized across channels:

    * ``task_id`` / ``context_id`` — identifiers; reuse the latter to continue
      a multi-turn dialog.
    * ``state`` — final task state (e.g. ``TASK_STATE_COMPLETED``,
      ``TASK_STATE_INPUT_REQUIRED``).
    * ``artifacts`` — every artifact the agent produced. For agents that emit
      only artifacts (A2A v1.0 hello-world style), the chat reply lives here.
    * ``status_message`` — the message attached to the final status, when
      present. For ``INPUT_REQUIRED`` this typically carries a form schema
      in ``parts[].data``; for ``FAILED``/``AUTH_REQUIRED`` it carries text.

    Channels we deliberately do NOT surface here:
      * ``task.history`` — caller can read it off the wire if needed; it
        often contains the agent's chain-of-thought, which an LLM caller
        usually does not want to relay to the user as "the agent's reply".
      * The ``message`` event channel — for the same reason: many agents
        push reasoning text there. If you need it, request a future field.
    """

    task_id: str = ""
    context_id: str = ""
    state: str = Field(..., description="A2A task final state, e.g. TASK_STATE_COMPLETED.")
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    status_message: StatusMessage | None = Field(
        default=None,
        description=(
            "Agent's message attached to the final status update; populated "
            "for INPUT_REQUIRED / FAILED / AUTH_REQUIRED, usually empty for "
            "COMPLETED."
        ),
    )
