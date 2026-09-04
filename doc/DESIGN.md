# A2A-MCP 设计文档

> 版本：v0.3（实现稿）
> 日期：2026-09-04
> 状态：已实现，校准文档

## 1. 背景与目标

### 1.1 背景

随着 [A2A（Agent-to-Agent）协议](https://a2a-protocol.org/) 的发布，多个 Agent 服务之间可以通过标准化协议互相发现、调用与协作。然而 MCP（Model Context Protocol）生态中的客户端（如 Claude Desktop、Cursor、各类 IDE 插件）本身**不直接支持 A2A 协议**。要让 MCP 客户端能够使用某个 A2A Agent，必须在中间架设一个适配层。

### 1.2 目标

构建一个 Python MCP Server，作为 **MCP 客户端 → A2A Agent** 的桥梁：

- **单一工具**：`call_agent`，完成"调用 A2A Agent 并返回结果"的全部动作
- **协议兼容**：同时支持 A2A **v1.0**（当前标准）与 **v0.3**（遗留系统），由 SDK 自动协商
- **传输分阶段**：第一阶段支持 **stdio**（MCP 默认），第二阶段支持 **Streamable HTTP**
- **简化调用**：对 MCP 客户端暴露的参数保持最小化（核心字段），复杂字段后续可补

### 1.3 非目标

- 不实现 A2A Server（不暴露 Agent 给他方调用）
- 不做 Agent 注册中心 / 服务发现
- 不做复杂的鉴权代理（MVP 透传 headers 即可）
- 不做持久化 / 任务队列

---

## 2. 总体架构

### 2.1 组件视图

```
┌──────────────────┐    stdio / HTTP     ┌──────────────────┐    A2A     ┌──────────────────┐
│   MCP Client     │ ◀────────────────▶ │   a2a-mcp        │ ◀────────▶ │   A2A Agent      │
│ (Claude Desktop, │     MCP 协议         │   (本项目)        │  v1.0/v0.3 │   (远端服务)      │
│  Cursor, IDEs)   │     JSON-RPC        │                  │            │                  │
└──────────────────┘                     └──────────────────┘            └──────────────────┘
```

### 2.2 数据流（一次 `call_agent` 调用）

```
MCP Client                                  a2a-mcp                                       A2A Agent
    │                                          │                                                │
    │  tools/call (call_agent, args)            │                                                │
    ├─────────────────────────────────────────▶│                                                │
    │                                          │  1. fetch /.well-known/agent.json              │
    │                                          ├───────────────────────────────────────────────▶│
    │                                          │◀──────────────── AgentCard ──────────────────┤
    │                                          │  2. negotiate protocol version (1.0 > 0.3)     │
    │                                          │                                                │
    │                                          │  3. send_message (Message, parts=[text])        │
    │                                          ├───────────────────────────────────────────────▶│
    │                                          │     (内部走流式客户端，聚合成最终 task 状态)     │
    │                                          │◀──────────────── Task (final) ────────────────┤
    │  tools/call result (text content)        │                                                │
    │◀─────────────────────────────────────────┤                                                │
```

### 2.3 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.10+ | MCP SDK 要求 |
| MCP SDK | `mcp[cli]`（FastMCP） | 装饰器暴露 tool |
| A2A SDK | `a2a-sdk` | 内置 ClientFactory + v0.3 兼容层 |
| HTTP 客户端 | `httpx`（a2a-sdk 依赖） | 拉取 AgentCard / 通信 |
| 异步运行时 | `asyncio` | MCP 与 a2a-sdk 均为 async |
| 包管理 | `uv`（推荐）/ `pip` | 行业惯例 |
| 项目布局 | `src/` layout | 防止本地包冲突 |

---

## 3. 协议设计

### 3.1 A2A 版本协商

| 配置 | 值 |
|------|---|
| `supported_protocol_versions` | `["1.0", "0.3"]` |
| `preferred_protocol_version` | `"1.0"` |

**协商机制**（由 `a2a-sdk` `ClientFactory` 自动完成）：

1. `A2ACardResolver.get_agent_card()` 拉取 Agent Card，提取该 Agent 声明支持的协议版本
2. `ClientFactory.create()` 选取客户端与 Agent 都支持的最高版本
3. 若 Agent 仅声明 0.3，SDK 自动启用 `a2a.compat.v0_3` 适配层

**关键差异**（SDK 内部处理，调用方无感）：

| 维度 | v0.3 | v1.0 |
|------|------|------|
| 方法名 | `message/send` | `SendMessage` |
| Part 结构 | discriminated union（`kind` 字段） | 扁平结构 |
| 枚举值 | `completed`、`working` | `TASK_STATE_COMPLETED`、`TASK_STATE_WORKING` |
| ID 格式 | `tasks/{id}` | UUID |
| 流事件 | `kind` discriminator | 无 discriminator |

### 3.2 MCP 传输分阶段

| 阶段 | 传输 | 启动方式 | 状态 |
|------|------|---------|------|
| MVP | stdio | `python -m a2a_mcp`（或显式 `--transport stdio`）| v0.1，已发布 |
| v0.3 | Streamable HTTP | `python -m a2a_mcp --transport streamable-http --host 0.0.0.0 --port 8866` | v0.3，已发布 |

切换传输**无需修改业务代码**，仅改变启动命令或 `--transport` 标志。

**配置优先级：**CLI flag > 环境变量 > 内置默认值。所有 HTTP 相关设置都支持两种方式覆盖：

```bash
# 通过 CLI 覆盖
python -m a2a_mcp --transport streamable-http --host 0.0.0.0 --port 9000 --path /api/mcp

# 通过环境变量（适合容器化部署）
A2A_MCP_TRANSPORT=streamable-http A2A_MCP_HTTP_PORT=9000 python -m a2a_mcp
```

**HTTP 模式约束（v0.3）：**
- 默认绑定 `127.0.0.1`；要远程访问需显式 `--host 0.0.0.0`（明示不安全，避免误暴露）
- 不带 auth；鉴权代理放到上游 nginx / Envoy（v0.6 再做 MCP 原生 auth）
- 单一进程、单 httpx 客户端；并发请求共享连接池，单请求超时仍受 `A2A_MCP_TIMEOUT` 控制

---

## 4. `call_agent` 工具设计

### 4.1 入参（核心字段）

按"核心字段，后续可补齐"原则，仅暴露以下参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `agent_url` | `str` (URL) | 是 | 目标 A2A Agent 服务根地址，例如 `http://localhost:10000` |
| `text` | `str` | 是 | 用户消息文本，作为 `Message` 的第一个 `TextPart` |
| `context_id` | `str` | 否 | 多轮对话上下文 ID；不传则 SDK 自动生成；多轮时复用同一个值 |
| `metadata` | `dict[str, Any]` | 否 | 自由格式键值对，挂到 A2A `Message.metadata` 上原样透传给 agent。Agent 只读它认识的 key，其余忽略。**常见用途**：tracing / correlation ID、tenant_id / user_id、locale、AB 分桶、feature flag。**a2a-mcp 不做任何检查或改写**，和协议上其他字段一样的纯透传 |

**示例（含 metadata）**：

```json
{
  "agent_url": "http://localhost:10000",
  "text": "What's the weather in Tokyo tomorrow?",
  "context_id": "ctx-abc-123",
  "metadata": {
    "tenant_id": "acme-corp",
    "user_id": "u-91827",
    "trace_id": "trace-2026-09-04-abc123",
    "locale": "zh-CN",
    "ab_bucket": "B"
  }
}
```

**后续可补齐字段**（不在 MVP）：`message_id`、`task_id`、`history_length`、`accepted_output_modes`、`blocking` 等。

### 4.2 返回值

`call_agent` 是 **transport，不是 synthesizer**——只把 agent 在协议各通道实际发出的内容透传出来，**不**做跨通道的合并 / fold / join。

**成功（COMPLETED，artifact 通道带回聊天回复）：**

```json
{
  "task_id": "8d2f1a4e-...",
  "context_id": "ctx-abc-123",
  "state": "TASK_STATE_COMPLETED",
  "artifacts": [
    {
      "artifact_id": "...",
      "name": "weather_report",
      "description": "",
      "parts": [{"text": "Tomorrow in Tokyo: cloudy, 18-24°C...", "url": null, "data": null, "filename": null, "media_type": null}]
    }
  ],
  "status_message": null
}
```

**成功（INPUT_REQUIRED，结构化表单 schema 在 `status_message.parts[].data`）：**

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
          "form_data": {"request_id": "...", "purpose": "显卡费用", "date": "", "amount": ""},
          "instructions": null
        },
        "url": null, "filename": null, "media_type": null
      }
    ]
  }
}
```

**字段含义：**

| 字段 | 来源 | 说明 |
|------|------|------|
| `task_id` | status_update / task 事件 | A2A Task ID，配合 `context_id` 做多轮 |
| `context_id` | status_update / task 事件 | 会话上下文 ID |
| `state` | 最后一次 status 的 state | 终态：`COMPLETED` / `FAILED` / `CANCELED` / `REJECTED` / `INPUT_REQUIRED` / `AUTH_REQUIRED` |
| `artifacts` | task.artifacts + artifact_update | 原样保留，按 `artifact_id` 取最后版本。**不做 fold、不做合并、不和 message 通道交叉** |
| `status_message` | status_update.status.message（或 task.status.message） | 仅当 agent 在终态挂了 message 时填充；结构化：`parts[].text` 和 `parts[].data` 任一可空 |

**主动不暴露的通道：**
- `task.history`——很多 agent 把 chain-of-thought 放在这里，混在 message 里。读 history 当"agent 回复"会把 CoT 泄漏给用户。
- `message` 事件通道——同上。
- 不再有 `agent_response` 这种合成的扁平字符串字段；调用方按 `state` 分流：

| 终态 | 客户端读哪个字段 |
|------|------------------|
| `COMPLETED` | `artifacts[].parts[].text` |
| `INPUT_REQUIRED` | `status_message.parts[].data`（form schema）+ `parts[].data.form_data` |
| `FAILED` / `REJECTED` | `status_message.parts[].text` |
| `AUTH_REQUIRED` | `status_message.parts[].text` |

**失败**（抛 MCP 错误）：

```
ToolError: A2A 调用失败: <错误类型>: <错误描述>
```

### 4.3 内部实现要点

- **流式聚合**：内部使用 `client.send_message()` 拿到事件迭代器，依次 `feed()` 给 `_ResponseAggregator`：遇到 terminal state（`COMPLETED` / `FAILED` / `CANCELED` / `REJECTED` / `INPUT_REQUIRED` / `AUTH_REQUIRED`）就停，最多再 drain 5s 处理跟在终态后面的 artifact。
- **超时控制**：默认 60s，通过环境变量 `A2A_MCP_TIMEOUT` 可配；超时抛 `A2ACallError`（→ MCP `ToolError`）。
- **artifact 合并策略**：按 `artifact_id` 取最后版本（latest-wins）。同 ID 的多次 `artifact_update` 视为流式增量。
- **protobuf Value 清洗**：artifact / status message 的 `data` oneof 是 `google.protobuf.struct_pb2.Value`，Pydantic 序列化直接拒绝。`_to_jsonable()` 把未设置的 Value 转 None，已设置的 Value 经 `MessageToDict()` 转 Python 原语。
- **刻意不做**：不读 `task.history`、不读 `message` 事件、不 fold artifact text 进任何字符串字段——这三个是设计上要避开的合成路径。

---

## 5. 错误处理

### 5.1 错误分类与处理策略

| 错误类别 | 来源 | 处理策略 |
|---------|------|---------|
| AgentCard 拉取失败（连接超时、404） | a2a-sdk | 抛 `ToolError`，提示用户检查 `agent_url` |
| 协议版本无交集 | a2a-sdk | 抛 `ToolError`，提示该 Agent 不可达 |
| A2A 业务错误（task failed） | Agent | 正常返回 `state=TASK_STATE_FAILED`，附带错误 message |
| MCP 客户端超时 | MCP | 由 MCP 客户端决定；服务端配置 `A2A_MCP_TIMEOUT` |
| 内部未捕获异常 | Python | 抛 `ToolError` 含 traceback |

### 5.2 日志格式

- 使用 Python `logging`（`logging.basicConfig`）
- 输出到 stderr（不污染 stdio 数据流）
- 格式：`%(asctime)s %(levelname)s %(name)s: %(message)s`

---

## 6. 项目结构

```
a2a-mcp/
├── pyproject.toml              # 项目元信息、依赖
├── README.md                   # 快速开始、示例
├── doc/
│   └── DESIGN.md               # 本文档
├── src/
│   └── a2a_mcp/
│       ├── __init__.py
│       ├── server.py           # FastMCP 入口，注册 call_agent
│       ├── a2a_client.py       # A2A 客户端封装（版本协商、流式聚合）
│       ├── types.py            # Pydantic 模型（CallAgentInput / Result）
│       └── config.py           # 环境变量配置（timeout、logging 等）
├── tests/
│   ├── test_a2a_client.py
│   └── test_server.py
└── examples/
    ├── claude_desktop_config.json
    └── mock_agent.py           # 本地测试用 mock A2A 服务
```

---

## 7. 安全考量

- **URL 校验**：禁止 `file://`、`javascript://` 等非 HTTP(S) scheme
- **SSRF 缓解（MVP 不做，v0.2 考虑）**：可选白名单环境变量 `A2A_MCP_ALLOWED_HOSTS`
- **凭据透传**（v0.2 考虑）：若需访问鉴权 Agent，允许通过 metadata 注入 bearer token
- **超时强制**：防止恶意 Agent 让服务端无限等待

---

## 8. 里程碑

| 版本 | 范围 | 状态 |
|------|------|------|
| v0.1 | stdio + 自动协商 + 流式聚合 + 核心字段（`agent_response` 合成） | 已发布（commits 46da7b5 之前） |
| v0.2 | **Pass-through 重构**：去掉 `agent_response` 合成，新增 `status_message` 结构化通道；INPUT_REQUIRED 的 form schema 暴露为 dict | 已发布（commits cfe576d / 3e2bcf1 / 000944f） |
| v0.3 | Streamable HTTP transport；CLI flags + 环境变量双轨配置 | 已发布（当前 commit） |
| v0.4 | 流式返回（增量 token 推送，需 MCP 侧 SSE 支持） | 待启动 |
| v0.5 | 多 Agent 注册中心（yaml 配置 + agent_id 路由） | 待启动 |
| v0.6 | 鉴权代理（Bearer / OAuth / mTLS 透传） | 待启动 |

---

## 9. 风险与开放问题

| 问题 | 说明 | 缓解 |
|------|------|------|
| a2a-sdk v1.0 API 仍在演进 | 1.0 alpha 期间 API 可能调整 | 锁定 minor 版本，升级时回归测试 |
| MCP tool 不支持真正流式 | MCP `tools/call` 是请求/响应 | 聚合为最终结果（已决策） |
| a2a-sdk gRPC/HTTP+REST 选择 | ClientFactory 选哪种 transport？ | 使用 `supported_protocol_versions` 协商时，由 SDK 自动选取 Agent 声明的 transport |
| 长任务阻塞 | Agent 处理慢时 MCP 客户端等待 | 60s 超时（可配），超时则返回部分结果 + task_id 供续接 |

---

## 10. 参考资料

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [A2A 协议规范](https://a2a-protocol.org/)
- [A2A Python SDK](https://github.com/a2aproject/a2a-python)
- [What's New in A2A v1.0](https://a2a-protocol.org/dev/whats-new-v1)
- [A2A Protocol Version History](https://deepwiki.com/a2aproject/A2A/6.3-protocol-version-history)