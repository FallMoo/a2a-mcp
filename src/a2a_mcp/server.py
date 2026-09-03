"""MCP server entrypoint.

Exposes a single tool `call_agent` that delegates to A2AClient. Supports
stdio transport (default) and streamable-http / sse transports when
launched through the mcp CLI (`mcp run server.py --transport ...`).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server.mcpserver import MCPServer

from .a2a_client import A2ACallError, A2AClient
from .config import Config, configure_logging
from .types import CallAgentInput, CallAgentResult

logger = logging.getLogger(__name__)

_SERVER_NAME = "a2a-mcp"
_SERVER_INSTRUCTIONS = (
    "a2a-mcp bridges to A2A (Agent-to-Agent) agents. Use the `call_agent` tool "
    "with a target agent_url and a text message to invoke any A2A-compliant "
    "agent and receive its final result."
)

mcp = MCPServer(
    name=_SERVER_NAME,
    instructions=_SERVER_INSTRUCTIONS,
)


@mcp.tool(name="call_agent", structured_output=True)
async def call_agent(
    agent_url: str,
    text: str,
    context_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> CallAgentResult:
    """Send a message to an A2A agent and return its final result.

    Args:
        agent_url: Root URL of the target A2A agent (http/https).
        text: User message text to send.
        context_id: Optional multi-turn dialog context ID; auto-generated if omitted.
        metadata: Optional free-form metadata forwarded to the agent.

    Returns:
        CallAgentResult with task_id, context_id, state, agent_response,
        and any artifacts produced by the agent.

    Raises:
        ToolError: when the A2A call fails (connection, protocol, agent error).
    """
    config: Config = mcp.request_context.lifespan_context["config"]  # type: ignore[attr-defined]
    client: A2AClient = mcp.request_context.lifespan_context["a2a_client"]  # type: ignore[attr-defined]
    params = CallAgentInput(
        agent_url=agent_url,
        text=text,
        context_id=context_id,
        metadata=metadata,
    )
    try:
        return await client.call(params)
    except A2ACallError as exc:
        logger.warning("call_agent failed: %s", exc)
        raise


def main() -> None:
    """Synchronous entrypoint for `python -m a2a_mcp` or the `a2a-mcp` script."""
    config = Config.from_env()
    configure_logging(config.log_level)
    logger.info("a2a-mcp starting (log_level=%s, timeout=%ds)", config.log_level, config.timeout)

    # Lifespan-managed shared resources.
    async def _lifespan(server: MCPServer):  # type: ignore[no-untyped-def]
        client = A2AClient(config)
        try:
            yield {"config": config, "a2a_client": client}
        finally:
            await client.aclose()

    mcp.run(transport="stdio", lifespan=_lifespan)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)


# ---------------------------------------------------------------------------
# Why `mcp.request_context.lifespan_context`?
#
# In mcp 2.x, per-request state (including lifespan-managed resources) is
# exposed via the active RequestContext. We attach the singleton A2AClient
# and Config through the lifespan callback so concurrent tool calls share
# the same httpx connection pool without re-creating it per call.
# ---------------------------------------------------------------------------


# Silence unused-import warnings under future refactors.
_ = asyncio