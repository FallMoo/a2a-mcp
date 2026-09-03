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
        A2A_MCP_TIMEOUT         - per-call timeout in seconds (default 60)
        A2A_MCP_LOG_LEVEL       - logging level (default INFO)
        A2A_MCP_PROTOCOL_BINDS  - comma-separated transport hints for A2A
                                  negotiation (default "JSONRPC,HTTP+JSON").
                                  GRPC is opt-in: requires `pip install a2a-sdk[grpc]`.
    """

    timeout: int = 60
    log_level: str = "INFO"
    protocol_bindings: tuple[str, ...] = ("JSONRPC", "HTTP+JSON")

    @classmethod
    def from_env(cls) -> "Config":
        binds = _get_str("A2A_MCP_PROTOCOL_BINDS", "JSONRPC,HTTP+JSON")
        bindings = tuple(b.strip() for b in binds.split(",") if b.strip())
        return cls(
            timeout=_get_int("A2A_MCP_TIMEOUT", 60),
            log_level=_get_str("A2A_MCP_LOG_LEVEL", "INFO").upper(),
            protocol_bindings=bindings or ("JSONRPC",),
        )


def configure_logging(level: str) -> None:
    """Configure root logger to stderr so stdio data stream is not polluted."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=__import__("sys").stderr,
    )