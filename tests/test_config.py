"""Unit tests for config parsing and CLI argument resolution.

No network — pure env-var and argparse exercise.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from a2a_mcp.config import Config
from a2a_mcp.server import _resolve_cli_overrides


@pytest.fixture
def clean_env(monkeypatch):
    """Strip all A2A_MCP_* vars before each test."""
    for key in list(os.environ):
        if key.startswith("A2A_MCP_"):
            monkeypatch.delenv(key, raising=False)


def test_config_defaults(clean_env):
    cfg = Config.from_env()
    assert cfg.timeout == 60
    assert cfg.log_level == "INFO"
    assert cfg.transport == "stdio"
    assert cfg.http_host == "127.0.0.1"
    assert cfg.http_port == 8000
    assert cfg.http_path == "/mcp"


def test_config_http_env(clean_env, monkeypatch):
    monkeypatch.setenv("A2A_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("A2A_MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("A2A_MCP_HTTP_PORT", "9999")
    monkeypatch.setenv("A2A_MCP_HTTP_PATH", "/custom-mcp")
    cfg = Config.from_env()
    assert cfg.transport == "streamable-http"
    assert cfg.http_host == "0.0.0.0"
    assert cfg.http_port == 9999
    assert cfg.http_path == "/custom-mcp"


def test_config_bad_port_falls_back_to_default(clean_env, monkeypatch):
    monkeypatch.setenv("A2A_MCP_HTTP_PORT", "not-a-number")
    cfg = Config.from_env()
    assert cfg.http_port == 8000


def test_config_log_level_uppercased(clean_env, monkeypatch):
    monkeypatch.setenv("A2A_MCP_LOG_LEVEL", "debug")
    cfg = Config.from_env()
    assert cfg.log_level == "DEBUG"


def test_cli_no_args(clean_env):
    """No CLI flags -> no overrides; env values stay intact."""
    overrides = _resolve_cli_overrides(argv=[])
    assert overrides == {}


def test_cli_transport_only(clean_env):
    overrides = _resolve_cli_overrides(argv=["--transport", "streamable-http"])
    assert overrides == {"transport": "streamable-http"}


def test_cli_all_http_flags(clean_env):
    overrides = _resolve_cli_overrides(
        argv=[
            "--transport", "streamable-http",
            "--host", "10.0.0.1",
            "--port", "9001",
            "--path", "/api/mcp",
        ]
    )
    assert overrides == {
        "transport": "streamable-http",
        "host": "10.0.0.1",
        "port": 9001,
        "path": "/api/mcp",
    }


def test_cli_rejects_unknown_transport(clean_env):
    with pytest.raises(SystemExit):
        _resolve_cli_overrides(argv=["--transport", "grpc"])


def test_cli_rejects_non_int_port(clean_env):
    with pytest.raises(SystemExit):
        _resolve_cli_overrides(argv=["--port", "abc"])
