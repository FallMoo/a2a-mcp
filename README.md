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
# Install
uv add "mcp[cli]" "a2a-sdk"

# Run with stdio transport
uv run mcp run src/a2a_mcp/server.py --transport stdio
```

Then configure your MCP client (e.g. Claude Desktop) to spawn this command.

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