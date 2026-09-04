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

# Run with stdio transport (default — for Claude Desktop, MCP Inspector)
uv run python -m a2a_mcp

# Or run with streamable-http transport (for remote MCP clients)
uv run python -m a2a_mcp --transport streamable-http --host 0.0.0.0 --port 8866
```

Then point your MCP client at it. For stdio, point Claude Desktop's MCP
server config at the `a2a-mcp` command. For streamable-http, point the
client at `http://<host>:<port>/mcp`.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `stdio` | MCP transport: `stdio` or `streamable-http` |
| `--host` | `127.0.0.1` | HTTP bind host (streamable-http only) |
| `--port` | `8866` | HTTP bind port (streamable-http only) |
| `--path` | `/mcp` | HTTP endpoint path (streamable-http only) |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_MCP_TIMEOUT` | `60` | Per-call timeout in seconds |
| `A2A_MCP_LOG_LEVEL` | `INFO` | Logging level; logs go to stderr |
| `A2A_MCP_TRANSPORT` | `stdio` | Same as `--transport`; CLI flag takes precedence |
| `A2A_MCP_HTTP_HOST` | `127.0.0.1` | Same as `--host`; CLI flag takes precedence |
| `A2A_MCP_HTTP_PORT` | `8866` | Same as `--port`; CLI flag takes precedence |
| `A2A_MCP_HTTP_PATH` | `/mcp` | Same as `--path`; CLI flag takes precedence |

Precedence for all of the above: **CLI flag > env var > built-in default**.

Transport negotiation (between a2a-mcp and the target A2A agent) is
automatic: at call time the SDK reads the target agent's AgentCard and
picks a binding both sides support (JSONRPC, HTTP+JSON, GRPC if
`a2a-sdk[grpc]` is installed).

## 🛠️ Tool: `call_agent`

### Input

| Field | Type | Required | Description |
|------|------|----------|-------------|
| `agent_url` | `str` (URL) | ✅ | Target A2A agent root URL |
| `text` | `str` | ✅ | User message text |
| `context_id` | `str` | ❌ | Multi-turn dialog context ID; reuse the same value to continue a session |
| `metadata` | `dict[str, Any]` | ❌ | Free-form key/value pairs attached to the A2A `Message.metadata` field and forwarded as-is to the agent. The agent reads only the keys it knows about; everything else is ignored. Typical uses: tracing IDs, tenant/role hints, locale, A/B bucket, feature flags. Most demo agents ignore this entirely; production agents usually use it for routing, auth context, or observability. |

### Example Call

```json
{
  "agent_url": "http://localhost:10000",
  "text": "Summarize the latest A2A spec in 3 bullet points",
  "context_id": "user-session-42"
}
```

### Example Call with `metadata`

```json
{
  "agent_url": "http://localhost:10000",
  "text": "Refund my order",
  "metadata": {
    "tenant_id": "acme-corp",
    "user_id": "u-91827",
    "trace_id": "trace-2026-09-04-abc123",
    "locale": "zh-CN",
    "ab_bucket": "B"
  }
}
```

The agent picks up the keys it cares about (e.g. `tenant_id` for
routing, `trace_id` for logging) and silently ignores the rest. a2a-mcp
neither inspects nor rewrites the dict — it's pure pass-through, same
as every other field on the wire.

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
        {"text": "• Discovery via Agent Card\n• Async tasks with streaming\n• JSON-RPC + HTTP+REST + gRPC transports", "raw": null, "url": null, "data": null, "metadata": null, "filename": null, "media_type": null}
      ]
    }
  ],
  "status_message": null
}
```

### Example Response — artifact carrying a binary file (image / PDF / report)

```json
{
  "task_id": "f1ecb522-...",
  "state": "TASK_STATE_COMPLETED",
  "artifacts": [
    {
      "artifact_id": "...",
      "name": "report",
      "parts": [
        {
          "text": null,
          "raw": "iVBORw0KGgoAA...AA==",   ← base64 string in JSON
          "url": null,
          "data": null,
          "metadata": {"source": "scanner", "page_count": 12},
          "filename": "report.pdf",
          "media_type": "application/pdf"
        }
      ]
    }
  ],
  "status_message": null
}
```

Each part has six nullable fields — one per A2A `Part` oneof / field:
`text`, `raw` (bytes — base64 in JSON), `url`, `data` (structured Value →
dict), `metadata` (Part-level free-form k/v), `filename`, `media_type`.
a2a-mcp surfaces all of them pass-through; pick whichever channel the
agent used.

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
- [x] v0.3 — Streamable HTTP transport (`--transport streamable-http`)
- [ ] v0.4 — True streaming via MCP progress events
- [ ] v0.5 — Multi-agent registry
- [ ] v0.6 — Auth passthrough (Bearer / OAuth / mTLS)

## 📄 License

TBD