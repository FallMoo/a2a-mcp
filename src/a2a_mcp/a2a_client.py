"""A2A client wrapper.

Responsibilities:
    1. Resolve AgentCard (auto-protocol negotiation between v1.0 and v0.3).
    2. Build and dispatch a SendMessageRequest.
    3. Iterate the streaming response, aggregating events into a final Task.
    4. Extract a normalized CallAgentResult for the MCP caller — by
       passing through the channels the agent actually emitted (artifacts,
       final status message) without folding or merging.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import Client, ClientConfig
from a2a.client.client_factory import ClientFactory
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Value as PbValue

from .config import Config
from .types import (
    ArtifactPart,
    ArtifactSummary,
    CallAgentInput,
    CallAgentResult,
    StatusMessage,
    StatusMessagePart,
)

logger = logging.getLogger(__name__)


def _to_jsonable(value: Any) -> Any:
    """Convert an a2a/protobuf value into something Pydantic can serialize.

    The a2a-sdk represents optional structured fields as
    ``google.protobuf.struct_pb2.Value``. Even when the field is unset,
    accessing it returns an empty ``Value`` (not None) which Pydantic's
    serializer rejects. Convert unset Values to None and set ones to a
    plain Python primitive via ``MessageToDict``. Pass everything else
    through unchanged.
    """
    if isinstance(value, PbValue):
        if value.WhichOneof("kind") is None:
            return None
        return MessageToDict(value)
    return value


def available_protocol_bindings() -> list[str]:
    """Return the protocol bindings the installed a2a-sdk can use.

    All three bindings the a2a-sdk supports (JSONRPC, HTTP+JSON, GRPC) are
    listed unconditionally because `a2a-sdk[grpc]` is a project dependency.
    ClientFactory passes these to the target agent's AgentCard negotiation
    to pick a compatible transport — the user never has to configure anything.
    """
    return ["JSONRPC", "HTTP+JSON", "GRPC"]


# Terminal task states (no further events expected). These end the
# aggregation loop in A2ACall._consume_stream.
_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_AUTH_REQUIRED",
}


class A2ACallError(RuntimeError):
    """Raised when an A2A call cannot complete. Surfaces to MCP as ToolError."""


class A2AClient:
    """Thin wrapper around a2a-sdk's ClientFactory with pass-through semantics.

    Each call_agent invocation gets its own client so AgentCard resolution and
    protocol negotiation are scoped to the current target agent URL.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        # Shared httpx client; will be closed on shutdown.
        self._httpx = httpx.AsyncClient(timeout=config.timeout)

    async def aclose(self) -> None:
        await self._httpx.aclose()

    async def call(self, params: CallAgentInput) -> CallAgentResult:
        url = str(params.agent_url).rstrip("/")
        logger.info("call_agent: agent_url=%s context_id=%s", url, params.context_id)

        # 1. Resolve AgentCard + build transport-aware client.
        card = await A2ACardResolver(self._httpx, base_url=url).get_agent_card()
        client_config = ClientConfig(
            streaming=True,
            httpx_client=self._httpx,
            supported_protocol_bindings=available_protocol_bindings(),
        )
        client = ClientFactory(client_config).create(card)

        # 2. Build the SendMessageRequest.
        message_id = uuid.uuid4().hex
        context_id = params.context_id or uuid.uuid4().hex
        message = _build_message(
            message_id=message_id,
            context_id=context_id,
            text=params.text,
            metadata=params.metadata,
        )
        request = _build_send_request(message)

        # 3. Iterate events until we see a terminal status (or the stream ends).
        try:
            aggregator = _ResponseAggregator()
            await self._consume_stream(client, request, aggregator)
        except Exception as exc:  # broad: surface as A2ACallError for MCP tool layer
            logger.exception("A2A stream consumption failed")
            raise A2ACallError(f"A2A call failed: {type(exc).__name__}: {exc}") from exc

        return aggregator.to_result()

    async def _consume_stream(
        self,
        client: Client,
        request: Any,
        aggregator: "_ResponseAggregator",
    ) -> None:
        """Iterate Client.send_message (which is an async iterator) with timeout."""
        iterator = client.send_message(request=request)
        try:
            while True:
                event = await asyncio.wait_for(
                    iterator.__anext__(), timeout=self._config.timeout
                )
                aggregator.feed(event)
                if aggregator.is_terminal:
                    # Drain a few more events in case artifacts arrive after status.
                    await self._drain_remaining(iterator, aggregator)
                    return
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            raise A2ACallError(
                f"A2A stream timed out after {self._config.timeout}s"
            ) from exc

    async def _drain_remaining(
        self,
        iterator: Any,
        aggregator: "_ResponseAggregator",
        budget: float = 5.0,
    ) -> None:
        """After a terminal status, drain trailing artifact events for a short window."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                aggregator.feed(event)
        except (StopAsyncIteration, asyncio.TimeoutError):
            return


def _build_message(
    *, message_id: str, context_id: str, text: str, metadata: dict[str, Any] | None
) -> Any:
    from a2a import types as a2a_types

    msg = a2a_types.Message(
        message_id=message_id,
        context_id=context_id,
        role=a2a_types.Role.ROLE_USER,
        parts=[a2a_types.Part(text=text)],
    )
    if metadata:
        msg.metadata.update(metadata)
    return msg


def _build_send_request(message: Any) -> Any:
    from a2a import types as a2a_types

    return a2a_types.SendMessageRequest(message=message)


# ---- Response aggregation ---------------------------------------------------


class _ResponseAggregator:
    """Collects StreamResponse events into the channels CallAgentResult exposes.

    Channels collected:
      * identifiers: task_id, context_id, state
      * artifacts: every artifact, latest-version-per-id (no fold / no merge)
      * status_message: the message attached to the final status update
        (typically only set on INPUT_REQUIRED / FAILED / AUTH_REQUIRED)

    Channels NOT collected: message-channel text and task.history. Many
    agents push chain-of-thought onto those, and we deliberately don't
    surface reasoning as "the agent's reply" — callers that need it should
    request a field explicitly in a future revision.
    """

    def __init__(self) -> None:
        self.task_id: str = ""
        self.context_id: str = ""
        self.state: str = "TASK_STATE_UNSPECIFIED"
        self.artifacts: dict[str, ArtifactSummary] = {}
        self.status_message: StatusMessage | None = None
        self._terminal_seen: bool = False

    @property
    def is_terminal(self) -> bool:
        return self._terminal_seen

    def feed(self, event: Any) -> None:
        """Apply one StreamResponse (protobuf message) to internal state."""
        from a2a import types as a2a_types

        # StreamResponse.WhichOneof("payload") -> 'task'|'message'|'status_update'|'artifact_update'
        payload = event.WhichOneof("payload")
        logger.debug("event payload=%s", payload)

        if payload == "task":
            self._absorb_task(event.task)

        elif payload == "status_update":
            update = event.status_update
            if update.task_id:
                self.task_id = update.task_id
            if update.context_id:
                self.context_id = update.context_id
            state_name = a2a_types.TaskState.Name(update.status.state)
            self.state = state_name
            if state_name in _TERMINAL_STATES:
                self._terminal_seen = True
            # Capture the final status message as a structured field. The
            # caller decides what to do with it (read .text, parse .data
            # as a form schema, etc.) — we don't synthesize or fold.
            if update.status.message is not None:
                sm = _to_status_message(update.status.message)
                if sm is not None:
                    self.status_message = sm

        elif payload == "artifact_update":
            # StreamResponse.artifact_update is a TaskArtifactUpdateEvent
            # wrapping a single Artifact; unwrap to keep _absorb_artifact
            # uniform with the path used by task.artifacts.
            self._absorb_artifact(event.artifact_update.artifact)

        # 'message' events: deliberately ignored. They are part of the
        # message-channel stream we do not surface (see class docstring).

    def _absorb_task(self, task: Any) -> None:
        from a2a import types as a2a_types

        if task.id:
            self.task_id = task.id
        if task.context_id:
            self.context_id = task.context_id

        if task.status and task.status.state:
            state_name = a2a_types.TaskState.Name(task.status.state)
            self.state = state_name
            if state_name in _TERMINAL_STATES:
                self._terminal_seen = True
        # A 'task' event can carry its own status.message (e.g. when an
        # INPUT_REQUIRED task arrives before any status_update). Treat it
        # the same way we'd treat a status_update.status.message.
        if (
            task.status
            and task.status.message is not None
            and self.status_message is None
        ):
            sm = _to_status_message(task.status.message)
            if sm is not None:
                self.status_message = sm

        # task.history is intentionally NOT inspected — many agents push
        # chain-of-thought onto the history channel, and we don't surface
        # that as "the agent's reply".

        for art in task.artifacts:
            self._absorb_artifact(art)

    def _absorb_artifact(self, art: Any) -> None:
        parts: list[ArtifactPart] = []
        for p in art.parts:
            raw = getattr(p, "raw", None) or None
            # `Part.metadata` is a google.protobuf.Struct; coerce to a plain
            # dict (or None) so Pydantic can serialize it.
            metadata = _to_jsonable(getattr(p, "metadata", None))
            if isinstance(metadata, dict) and not metadata:
                metadata = None
            parts.append(
                ArtifactPart(
                    text=getattr(p, "text", None) or None,
                    raw=raw if raw else None,
                    url=getattr(p, "url", None) or None,
                    data=_to_jsonable(getattr(p, "data", None)),
                    metadata=metadata,
                    filename=getattr(p, "filename", None) or None,
                    media_type=getattr(p, "media_type", None) or None,
                )
            )
        summary = ArtifactSummary(
            artifact_id=art.artifact_id or uuid.uuid4().hex,
            name=art.name or "",
            description=art.description or "",
            parts=parts,
        )
        # Keep the latest version per artifact_id.
        self.artifacts[summary.artifact_id] = summary

    def to_result(self) -> CallAgentResult:
        return CallAgentResult(
            task_id=self.task_id,
            context_id=self.context_id,
            state=self.state,
            artifacts=list(self.artifacts.values()),
            status_message=self.status_message,
        )


def _to_status_message(msg: Any) -> StatusMessage | None:
    """Convert an a2a.types.Message (or protobuf equivalent) to StatusMessage.

    Returns None if the message has no parts with content — the protobuf
    spec always returns an empty Message container (not None) for an unset
    status.message, but we'd rather surface ``None`` to callers than an
    empty ``{"role":"","parts":[]}`` object.
    """
    from a2a import types as a2a_types

    role = a2a_types.Role.Name(msg.role) if msg.role else ""
    parts: list[StatusMessagePart] = []
    for p in msg.parts:
        raw = getattr(p, "raw", None) or None
        metadata = _to_jsonable(getattr(p, "metadata", None))
        if isinstance(metadata, dict) and not metadata:
            metadata = None
        parts.append(
            StatusMessagePart(
                text=getattr(p, "text", None) or None,
                raw=raw if raw else None,
                data=_to_jsonable(getattr(p, "data", None)),
                metadata=metadata,
                url=getattr(p, "url", None) or None,
                filename=getattr(p, "filename", None) or None,
                media_type=getattr(p, "media_type", None) or None,
            )
        )
    if not parts:
        return None
    return StatusMessage(role=role, parts=parts)
