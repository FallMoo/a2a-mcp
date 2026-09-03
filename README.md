# a2a-mcp

> A lightweight **MCP (Model Context Protocol) Server** that bridges MCP clients to **A2A (Agent-to-Agent) Agent** services.
> Exposes a single tool — `call_agent` — that sends a request to an A2A-compliant agent and returns the result, **without** MCP clients needing to speak A2A themselves.

## ✨ Features

- 🔌 **Single-tool API**: `call_agent` — text in, agent response out
- 🔄 **A2A v1.0 + v0.3 dual support** with automatic protocol negotiation
- 📡 **stdio transport** (MVP) — plug into Claude Desktop, MCP Inspector, etc.
- 🌐 **Streamable HTTP** transport planned
- 🐍 **Python 3.10+**, FastMCP + `a2a-sdk`

## 🚀 Quick Start

```bash
# Install (from project root)
uv sync --extra dev

# Run with stdio transport
uv run python -m a2a_mcp
```

Then configure your MCP client (e.g. Claude Desktop) — see [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_MCP_TIMEOUT` | `60` | Per-call timeout in seconds |
| `A2A_MCP_LOG_LEVEL` | `INFO` | Logging level; logs go to stderr |
| `A2A_MCP_PROTOCOL_BINDS` | `JSONRPC,GRPC,HTTP+JSON` | Ordered transport hints for A2A negotiation |

## 🛠️ Tool: `call_agent`

### Input

| Field | Type | Required | Description |
|------|------|----------|-------------|
| `agent_url` | `str` (URL) | ✅ | Target A2A agent root URL |
| `text` | `str` | ✅ | User message text |
| `context_id` | `str` | ❌ | Multi-turn dialog context ID |
| `metadata` | `dict` | ❌ | Free-form metadata forwarded to agent |

### Example Call

```json
{
  "agent_url": "http://localhost:10000",
  "text": "Summarize the latest A2A spec in 3 bullet points",
  "context_id": "user-session-42"
}
```

### Example Response

```json
{
  "task_id": "8d2f1a4e-...",
  "context_id": "user-session-42",
  "state": "TASK_STATE_COMPLETED",
  "agent_response": "• Discovery via Agent Card\n• Async tasks with streaming\n• JSON-RPC + HTTP+REST + gRPC transports",
  "artifacts": []
}
```

## 📚 Documentation

- [Design document](doc/DESIGN.md) — architecture, protocol negotiation, error handling, roadmap

## 🛣️ Roadmap

- [x] v0.1 — stdio + auto protocol negotiation + streamed aggregation
- [ ] v0.2 — Streamable HTTP transport
- [ ] v0.3 — True streaming via MCP progress events
- [ ] v0.4 — Multi-agent registry
- [ ] v0.5 — Auth passthrough (Bearer / OAuth / mTLS)

## 📄 License

TBD