"""MCP server entrypoint.

Exposes a single tool `call_agent` that delegates to A2AClient. Supports
two MCP transports:

  * ``stdio`` (default) — for Claude Desktop, MCP Inspector, etc.
  * ``streamable-http`` — for remote MCP clients; binds a uvicorn server.

The transport is selected by ``--transport`` on the CLI or
``A2A_MCP_TRANSPORT`` in the environment. HTTP-mode host / port / path
follow the same precedence: CLI flag > env var > default.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

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

# CLI defaults; env vars (read via Config.from_env) override when CLI flags
# are not provided, so existing deployments keep working without flags.
_HTTP_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8000,
    "path": "/mcp",
}


def _make_lifespan() -> Callable[[MCPServer], AbstractAsyncContextManager[dict[str, object]]]:
    """Build the lifespan async-context-manager that owns the singleton A2AClient.

    Resolved Config is read at server-start time (after env is parsed and
    logging is configured) so a single Config object is shared by every tool
    call for the lifetime of the server. The returned async-context-manager
    yields the per-server state dict that `call_agent` reads via its injected
    `Context`.
    """
    config = Config.from_env()
    configure_logging(config.log_level)
    logger.info(
        "a2a-mcp starting (log_level=%s, timeout=%ds)",
        config.log_level,
        config.timeout,
    )

    @asynccontextmanager
    async def _lifespan(server: MCPServer) -> AsyncIterator[dict[str, object]]:
        client = A2AClient(config)
        try:
            yield {"config": config, "a2a_client": client}
        finally:
            await client.aclose()

    return _lifespan


mcp = MCPServer(
    name=_SERVER_NAME,
    instructions=_SERVER_INSTRUCTIONS,
    lifespan=_make_lifespan(),  # applied at construction time in mcp 2.x
)


@mcp.tool(name="call_agent", structured_output=True)
async def call_agent(
    agent_url: str,
    text: str,
    context_id: str | None = None,
    metadata: dict[str, object] | None = None,
    ctx: Context | None = None,
) -> CallAgentResult:
    """Send a message to an A2A agent and return its final result.

    Args:
        agent_url: Root URL of the target A2A agent (http/https).
        text: User message text to send.
        context_id: Optional multi-turn dialog context ID; auto-generated if omitted.
        metadata: Optional free-form metadata forwarded to the agent.

    Returns:
        CallAgentResult with task_id, context_id, state, artifacts, and the
        structured status_message attached to the final status (if any).

    Raises:
        ToolError: when the A2A call fails (connection, protocol, agent error).
    """
    # The framework injects a per-request Context; the lifespan-managed
    # A2AClient and Config hang off its request_context.
    assert ctx is not None, "call_agent requires a Context (injected by the framework)"
    config: Config = ctx.request_context.lifespan_context["config"]  # type: ignore[index]
    client: A2AClient = ctx.request_context.lifespan_context["a2a_client"]  # type: ignore[index]
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


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="a2a-mcp",
        description="MCP server that bridges MCP clients to A2A agents.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="MCP transport (default: stdio; or $A2A_MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP host for streamable-http (default: 127.0.0.1; or $A2A_MCP_HTTP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP port for streamable-http (default: 8000; or $A2A_MCP_HTTP_PORT).",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Streamable-HTTP path (default: /mcp; or $A2A_MCP_HTTP_PATH).",
    )
    return parser


def _resolve_cli_overrides(argv: list[str] | None = None) -> dict[str, object]:
    """Parse argv, returning overrides for the bits that were explicitly set.

    Anything not on the CLI leaves the env-var-driven Config values intact.
    The `argv` parameter is exposed for tests; `main()` passes nothing and
    argparse falls back to ``sys.argv[1:]``.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    overrides: dict[str, object] = {}
    if args.transport is not None:
        overrides["transport"] = args.transport
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.path is not None:
        overrides["path"] = args.path
    return overrides


def main() -> None:
    """Synchronous entrypoint for `python -m a2a_mcp` or the `a2a-mcp` script."""
    cli = _resolve_cli_overrides()
    config = Config.from_env()

    transport = cli.get("transport", config.transport)
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    # streamable-http branch
    host = cli.get("host", config.http_host)
    port = cli.get("port", config.http_port)
    path = cli.get("path", config.http_path)
    logger.info(
        "a2a-mcp listening on http://%s:%d%s (streamable-http)",
        host,
        port,
        path,
    )
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path=path,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)


# ---------------------------------------------------------------------------
# Design notes:
#
#   * In mcp 2.x, per-request state (including lifespan-managed resources)
#     is exposed via the active RequestContext that the framework injects
#     into tool functions. `MCPServer` itself does not have a
#     `request_context` attribute; only the per-request Context does.
#     Likewise, `lifespan` is a constructor argument on `MCPServer` — it
#     cannot be passed to `mcp.run()` the way it was in earlier versions.
#
#   * For HTTP transport we keep the same singleton A2AClient on the
#     lifespan, so concurrent tool calls share one httpx connection pool.
#     The singleton's per-call timeout (`A2A_MCP_TIMEOUT`) still bounds
#     each request — long-running agents should use multi-turn instead.
#
#   * CLI flags take precedence over env vars; env vars take precedence
#     over the hard-coded defaults. This lets operators override the
#     `.mcp.json` baked-in settings without editing the file.
# ---------------------------------------------------------------------------
