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
        A2A_MCP_TIMEOUT    - per-call timeout in seconds (default 60)
        A2A_MCP_LOG_LEVEL  - logging level (default INFO)

    Protocol bindings are NOT a configuration knob: the a2a-sdk negotiates the
    transport from the target Agent's AgentCard at call time. We pass the
    full set of bindings the installed SDK supports (gRPC included when its
    optional dependencies are installed).
    """

    timeout: int = 60
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            timeout=_get_int("A2A_MCP_TIMEOUT", 60),
            log_level=_get_str("A2A_MCP_LOG_LEVEL", "INFO").upper(),
        )


def configure_logging(level: str) -> None:
    """Configure root logger to stderr so stdio data stream is not polluted."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=__import__("sys").stderr,
    )