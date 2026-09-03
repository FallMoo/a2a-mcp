"""A2A client wrapper.

Responsibilities:
    1. Resolve AgentCard (auto-protocol negotiation between v1.0 and v0.3).
    2. Build and dispatch a SendMessageRequest.
    3. Iterate the streaming response, aggregating events into a final Task.
    4. Extract a normalized CallAgentResult for the MCP caller.
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

from .config import Config
from .types import ArtifactPart, ArtifactSummary, CallAgentInput, CallAgentResult

logger = logging.getLogger(__name__)


def available_protocol_bindings() -> list[str]:
    """Return the protocol bindings the installed a2a-sdk can use.

    All three bindings the a2a-sdk supports (JSONRPC, HTTP+JSON, GRPC) are
    listed unconditionally because `a2a-sdk[grpc]` is a project dependency.
    ClientFactory passes these to the target agent's AgentCard negotiation
    to pick a compatible transport — the user never has to configure anything.
    """
    return ["JSONRPC", "HTTP+JSON", "GRPC"]


# Terminal task states (no further events expected). These end the aggregation loop.
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
    """Thin wrapper around a2a-sdk's ClientFactory with aggregated semantics.

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
    """Collects StreamResponse events and produces a final CallAgentResult."""

    def __init__(self) -> None:
        self.task_id: str = ""
        self.context_id: str = ""
        self.state: str = "TASK_STATE_UNSPECIFIED"
        self.agent_text_parts: list[str] = []
        self.artifacts: dict[str, ArtifactSummary] = {}
        self._terminal_seen: bool = False
        self._has_error_message: bool = False
        self._error_text: str = ""

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
            task = event.task
            self._absorb_task(task)

        elif payload == "message":
            msg = event.message
            if msg.context_id:
                self.context_id = msg.context_id
            for part in msg.parts:
                text = getattr(part, "text", None)
                if text:
                    self.agent_text_parts.append(text)

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
            # Capture agent message embedded in status (e.g. failed status carries reason).
            status_msg = update.status.message
            if status_msg is not None:
                for part in status_msg.parts:
                    text = getattr(part, "text", None)
                    if text:
                        self._error_text = text
                        if state_name in {
                            "TASK_STATE_FAILED",
                            "TASK_STATE_REJECTED",
                        }:
                            self._has_error_message = True

        elif payload == "artifact_update":
            # StreamResponse.artifact_update is a TaskArtifactUpdateEvent
            # wrapping a single Artifact; unwrap to keep _absorb_artifact
            # uniform with the path used by task.artifacts.
            self._absorb_artifact(event.artifact_update.artifact)

    def _absorb_task(self, task: Any) -> None:
        if task.id:
            self.task_id = task.id
        if task.context_id:
            self.context_id = task.context_id
        from a2a import types as a2a_types

        if task.status and task.status.state:
            state_name = a2a_types.TaskState.Name(task.status.state)
            self.state = state_name
            if state_name in _TERMINAL_STATES:
                self._terminal_seen = True
        # History may contain agent-side messages already produced.
        for msg in task.history:
            if msg.role == a2a_types.Role.ROLE_AGENT:
                for part in msg.parts:
                    text = getattr(part, "text", None)
                    if text:
                        self.agent_text_parts.append(text)
        for art in task.artifacts:
            self._absorb_artifact(art)

    def _absorb_artifact(self, art: Any) -> None:
        parts: list[ArtifactPart] = []
        for p in art.parts:
            parts.append(
                ArtifactPart(
                    text=getattr(p, "text", None) or None,
                    url=getattr(p, "url", None) or None,
                    data=getattr(p, "data", None) or None,
                    filename=getattr(p, "filename", None) or None,
                    media_type=getattr(p, "media_type", None) or None,
                )
            )
            text = getattr(p, "text", None)
            if text:
                # Fold artifact text into agent_response so MCP callers see
                # the agent's full textual reply, even when the agent only
                # emits artifacts (e.g. the A2A v1.0 hello-world reference).
                self.agent_text_parts.append(text)
        summary = ArtifactSummary(
            artifact_id=art.artifact_id or uuid.uuid4().hex,
            name=art.name or "",
            description=art.description or "",
            parts=parts,
        )
        # Keep the latest version per artifact_id.
        self.artifacts[summary.artifact_id] = summary

    def to_result(self) -> CallAgentResult:
        agent_response = "\n".join(t for t in self.agent_text_parts if t)
        if self._has_error_message and self._error_text:
            agent_response = self._error_text
        return CallAgentResult(
            task_id=self.task_id,
            context_id=self.context_id,
            state=self.state,
            agent_response=agent_response,
            artifacts=list(self.artifacts.values()),
        )