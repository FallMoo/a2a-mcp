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

Then point your MCP client (e.g. Claude Desktop) at this command via its MCP server config.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_MCP_TIMEOUT` | `60` | Per-call timeout in seconds |
| `A2A_MCP_LOG_LEVEL` | `INFO` | Logging level; logs go to stderr |

Transport negotiation is automatic: at call time the SDK reads the target
agent's AgentCard and picks a binding both sides support (JSONRPC, HTTP+JSON,
GRPC if `a2a-sdk[grpc]` is installed).

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

### Example Response — successful chat reply (artifact channel)

```json
{
  "task_id": "8d2f1a4e-...",
  "context_id": "user-session-42",
  "state": "TASK_STATE_COMPLETED",
  "artifacts": [
    {
      "artifact_id": "...",
      "name": "summary",
      "description": "",
      "parts": [
        {"text": "• Discovery via Agent Card\n• Async tasks with streaming\n• JSON-RPC + HTTP+REST + gRPC transports", "url": null, "data": null, "filename": null, "media_type": null}
      ]
    }
  ],
  "status_message": null
}
```

### Example Response — INPUT_REQUIRED (structured form schema)

```json
{
  "task_id": "f1ecb522-...",
  "context_id": "f0a142c7...",
  "state": "TASK_STATE_INPUT_REQUIRED",
  "artifacts": [],
  "status_message": {
    "role": "ROLE_AGENT",
    "parts": [
      {
        "text": null,
        "data": {
          "type": "form",
          "form": {"type": "object", "required": ["request_id", "date", "amount", "purpose"], "properties": {...}},
          "form_data": {"request_id": "request_id_5011658", "purpose": "显卡费用", "date": "", "amount": ""},
          "instructions": null
        },
        "url": null, "filename": null, "media_type": null
      }
    ]
  }
}
```

### Result channels

`call_agent` is a **transport**, not a synthesizer — it surfaces only what the
agent actually emitted, on the channel it was emitted on. There is no
flattened "agent_response" string; callers pick the channel that fits.

| Field | Always set? | Meaning |
|-------|-------------|---------|
| `task_id` | yes | A2A task ID; reuse via `context_id` for multi-turn |
| `context_id` | yes | Dialog context ID |
| `state` | yes | Final task state — `TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_AUTH_REQUIRED`, `TASK_STATE_CANCELED`, `TASK_STATE_REJECTED` |
| `artifacts` | yes (may be `[]`) | Raw artifacts, latest-version-per-id. For agents that emit only artifacts (A2A v1.0 hello-world style), the chat reply lives here in `parts[].text`. |
| `status_message` | only when the agent attached a message to the final status | Structured message with `parts[].text` and `parts[].data`. Typical for `INPUT_REQUIRED` (form schema in `data`), `FAILED` (reason in `text`), `AUTH_REQUIRED` (challenge in `text`). For plain `COMPLETED` it is usually `null`. |

** Channels we deliberately do NOT surface:** `task.history` (often contains
chain-of-thought) and the `message` event channel (same reason). If a future
use case needs them, add an explicit opt-in field rather than re-synthesizing.

**Client recipe by state:**

- `TASK_STATE_COMPLETED` → read `artifacts[].parts[].text` for the chat reply
- `TASK_STATE_INPUT_REQUIRED` → parse `status_message.parts[].data` for the form schema and `form_data`
- `TASK_STATE_FAILED` / `TASK_STATE_REJECTED` → read `status_message.parts[].text` for the reason
- `TASK_STATE_AUTH_REQUIRED` → read `status_message.parts[].text` for the challenge

## 📚 Documentation

- [Design document](doc/DESIGN.md) — architecture, protocol negotiation, error handling, roadmap

## 🛣️ Roadmap

- [x] v0.1 — stdio + auto protocol negotiation + streamed aggregation
- [x] v0.2 — Pass-through response model: `artifacts` + `status_message` (no `agent_response` synthesis); INPUT_REQUIRED form schema exposed structured
- [ ] v0.3 — Streamable HTTP transport
- [ ] v0.4 — True streaming via MCP progress events
- [ ] v0.5 — Multi-agent registry
- [ ] v0.6 — Auth passthrough (Bearer / OAuth / mTLS)

## 📄 License

TBD