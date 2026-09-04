"""Runtime configuration for a2a-mcp.

Configuration is sourced from environment variables (no file-based config in v0.1).
Defaults are tuned for stdio transport and short-lived MCP tool calls.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration.

    Environment variables:
        A2A_MCP_TIMEOUT       - per-call timeout in seconds (default 60)
        A2A_MCP_LOG_LEVEL     - logging level (default INFO)
        A2A_MCP_TRANSPORT     - MCP transport: "stdio" (default) or
                                "streamable-http"
        A2A_MCP_HTTP_HOST     - host to bind for streamable-http (default
                                127.0.0.1)
        A2A_MCP_HTTP_PORT     - port to bind for streamable-http (default
                                8866 — picked to dodge the usual 8000/
                                8080/8888 clashes with dev services)
        A2A_MCP_HTTP_PATH     - URL path for the MCP endpoint (default
                                "/mcp")

    Protocol bindings are NOT a configuration knob: the a2a-sdk negotiates
    the transport from the target Agent's AgentCard at call time. We pass
    the full set of bindings the installed SDK supports (gRPC included
    when its optional dependencies are installed).
    """

    timeout: int = 60
    log_level: str = "INFO"
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8866
    http_path: str = "/mcp"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            timeout=_get_int("A2A_MCP_TIMEOUT", 60),
            log_level=_get_str("A2A_MCP_LOG_LEVEL", "INFO").upper(),
            transport=_get_str("A2A_MCP_TRANSPORT", "stdio").lower(),
            http_host=_get_str("A2A_MCP_HTTP_HOST", "127.0.0.1"),
            http_port=_get_int("A2A_MCP_HTTP_PORT", 8866),
            http_path=_get_str("A2A_MCP_HTTP_PATH", "/mcp"),
        )


def configure_logging(level: str) -> None:
    """Configure root logger to stderr so stdio data stream is not polluted."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=__import__("sys").stderr,
    )