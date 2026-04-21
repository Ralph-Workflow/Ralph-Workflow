# MCP Package Architecture

The `ralph/mcp/` package is organized into five sub-packages that make the boundaries between Ralph's roles as MCP server and MCP client explicit.

## Sub-packages

### `ralph.mcp.upstream/` — MCP Client (Ralph → external servers)

Ralph acts as an **MCP client** when talking to user-defined upstream servers configured in `mcp.toml`.

| File | Purpose |
|------|---------|
| `client.py` | `HttpUpstreamClient`, `StdioUpstreamClient`, `make_upstream_client` |
| `config.py` | `UpstreamMcpServer` model, config loading/serialization |
| `models.py` | `UpstreamTool`, `UpstreamCallError` |
| `registry.py` | `UpstreamRegistry`, `ProxiedTool` — tracks tools from upstream servers |
| `validation.py` | Preflight validation of upstream servers (`validate_upstream_mcp_servers`) |
| `agent_probe.py` | Agent transport probe (`probe_agent_transports`) |

**Canonical import path:** `from ralph.mcp.upstream import ...` or `from ralph.mcp.upstream.<module> import ...`

### `ralph.mcp.tools/` — MCP Server Tools (Ralph → agents)

Ralph acts as an **MCP server** when advertising tools to connected AI agents.

| File | Purpose |
|------|---------|
| `names.py` | Tool name constants (`RALPH_MCP_SERVER_NAME`, `READ_FILE_TOOL`, etc.) |
| `bridge.py` | `ToolBridge` registry and `build_ralph_tool_registry` |
| `workspace.py` | `handle_read_file`, `handle_write_file`, `handle_list_directory`, etc. |
| `git_read.py` | `handle_git_status`, `handle_git_diff`, `handle_git_log`, `handle_git_show` |
| `exec.py` | `handle_exec_command` with command blacklist |
| `artifact.py` | `handle_submit_artifact`, `handle_submit_plan_section`, `handle_finalize_plan`, etc. |
| `coordination.py` | `handle_report_progress`, `handle_declare_complete`, `handle_coordinate`, `handle_read_env` |
| `websearch.py` | `handle_web_search` |

**Canonical import path:** `from ralph.mcp.tools import ...` or `from ralph.mcp.tools.<module> import ...`

### `ralph.mcp.artifacts/` — Artifact Storage

Persistent artifact storage and per-type validators. Used by **both** Ralph's server-side tool handlers and the upstream client.

| File | Purpose |
|------|---------|
| `store.py` | `Artifact`, `submit_artifact`, `get_artifact`, `list_artifacts`, `update_artifact`, `delete_artifact` |
| `file_backend.py` | `FileBackend`, `PathFileBackend`, `DEFAULT_FILE_BACKEND` |
| `plan.py` | `PlanArtifact`, `validate_plan_artifact`, `finalize_plan_draft`, etc. |
| `commit_message.py` | Commit message artifact helpers |
| `development_result.py` | Development result validation |
| `policy_outcomes.py` | `is_policy_approved` — shared policy interpretation |
| `audit_adapter.py` | `RalphAuditSinkAdapter`, audit record translation |
| `bridge.py` | `MCPBridge`, `BridgeConfig` — phase system ↔ MCP bridge |

**Canonical import path:** `from ralph.mcp.artifacts import ...` or `from ralph.mcp.artifacts.<module> import ...`

### `ralph.mcp.protocol/` — Shared Protocol Plumbing

Used by **both** the MCP server (Ralph → agents) and upstream client (Ralph → external MCPs).

| File | Purpose |
|------|---------|
| `transport.py` | `MCPTransport`, `StdioTransport`, `MCPMessage`, `TransportError` |
| `session.py` | `AgentSession`, `session_has_capability` |
| `capability_mapping.py` | `Capability`, `McpCapability`, `AccessMode`, policy mapping functions |
| `env.py` | `McpEnvVar`, environment variable constants |
| `startup.py` | Preflight helpers, `HeartbeatPolicy`, `access_mode_for_drain` |

**Canonical import path:** `from ralph.mcp.protocol import ...` or `from ralph.mcp.protocol.<module> import ...`

### `ralph.mcp.server/` — Standalone MCP Server

The standalone `ralph-mcp` runtime (not changed in this reorganization).

| File | Purpose |
|------|---------|
| `runtime.py` | Server runtime (`build_standalone_http_server`, `build_fastmcp_server`) |
| `factory.py` / `factory_impl.py` | Server factory |
| `lifecycle.py` | Server lifecycle; spawns the standalone process via `ProcessManager` |
| `__main__.py` | Entry point |

**Canonical import path:** `from ralph.mcp.server import ...` or `from ralph.mcp.server.<module> import ...`

## Canonical Import Paths

The following table lists the canonical import path for each public symbol:

| Symbol | Canonical Import |
|--------|-----------------|
| `MCPTransport`, `StdioTransport`, `TransportError` | `from ralph.mcp.protocol.transport import ...` |
| `AgentSession`, `session_has_capability` | `from ralph.mcp.protocol.session import ...` |
| `Capability`, `McpCapability`, `AccessMode`, etc. | `from ralph.mcp.protocol.capability_mapping import ...` |
| `McpEnvVar`, env constants | `from ralph.mcp.protocol.env import ...` |
| `HeartbeatPolicy`, `access_mode_for_drain`, etc. | `from ralph.mcp.protocol.startup import ...` |
| `ToolBridge`, `ToolDefinition`, `build_ralph_tool_registry` | `from ralph.mcp.tools.bridge import ...` |
| `READ_FILE_TOOL`, `WRITE_FILE_TOOL`, etc. | `from ralph.mcp.tools.names import ...` |
| `handle_read_file`, `handle_write_file`, etc. | `from ralph.mcp.tools.workspace import ...` |
| `handle_git_status`, `handle_git_diff`, etc. | `from ralph.mcp.tools.git_read import ...` |
| `handle_exec_command` | `from ralph.mcp.tools.exec import ...` |
| `handle_submit_artifact`, `handle_submit_plan_section`, etc. | `from ralph.mcp.tools.artifact import ...` |
| `handle_report_progress`, `handle_declare_complete`, etc. | `from ralph.mcp.tools.coordination import ...` |
| `handle_web_search` | `from ralph.mcp.tools.websearch import ...` |
| `HttpUpstreamClient`, `StdioUpstreamClient`, `make_upstream_client` | `from ralph.mcp.upstream.client import ...` |
| `UpstreamMcpServer`, `load_upstream_mcp_servers`, etc. | `from ralph.mcp.upstream.config import ...` |
| `UpstreamTool`, `UpstreamCallError` | `from ralph.mcp.upstream.models import ...` |
| `UpstreamRegistry`, `ProxiedTool` | `from ralph.mcp.upstream.registry import ...` |
| `validate_upstream_mcp_servers` | `from ralph.mcp.upstream.validation import ...` |
| `probe_agent_transports` | `from ralph.mcp.upstream.agent_probe import ...` |
| `Artifact`, `submit_artifact`, etc. | `from ralph.mcp.artifacts.store import ...` |
| `MCPBridge`, `BridgeConfig` | `from ralph.mcp.artifacts.bridge import ...` |
| `FileBackend`, `PathFileBackend` | `from ralph.mcp.artifacts.file_backend import ...` |
| `RalphAuditSinkAdapter` | `from ralph.mcp.artifacts.audit_adapter import ...` |
| `PlanArtifact`, `validate_plan_artifact`, etc. | `from ralph.mcp.artifacts.plan import ...` |
| `is_policy_approved` | `from ralph.mcp.artifacts.policy_outcomes import ...` |
| `commit_message_artifact_path`, etc. | `from ralph.mcp.artifacts.commit_message import ...` |
| `normalize_development_result_content` | `from ralph.mcp.artifacts.development_result import ...` |

## Package Directory Structure

```
ralph/mcp/
├── __init__.py          # Public API: MCPBridge, ToolBridge, access_mode_for_drain, etc.
├── ARCHITECTURE.md      # This file
├── artifacts/           # Artifact storage (both Ralph-as-server and Ralph-as-client)
│   ├── __init__.py
│   ├── audit_adapter.py
│   ├── bridge.py
│   ├── commit_message.py
│   ├── development_result.py
│   ├── file_backend.py
│   ├── plan.py
│   ├── policy_outcomes.py
│   └── store.py
├── protocol/            # Shared protocol plumbing (both server and client)
│   ├── __init__.py
│   ├── capability_mapping.py
│   ├── env.py
│   ├── session.py
│   ├── startup.py
│   └── transport.py
├── server/              # Ralph-as-MCP-server runtime
│   ├── __init__.py
│   ├── __main__.py
│   ├── factory.py
│   ├── factory_impl.py
│   ├── lifecycle.py
│   └── runtime.py
├── tools/               # Ralph-as-MCP-server tools (Ralph → agents)
│   ├── __init__.py
│   ├── artifact.py
│   ├── bridge.py
│   ├── coordination.py
│   ├── exec.py
│   ├── git_read.py
│   ├── names.py
│   ├── websearch.py
│   └── workspace.py
└── upstream/            # Ralph-as-MCP-client (Ralph → external servers)
    ├── __init__.py
    ├── agent_probe.py
    ├── client.py
    ├── config.py
    ├── models.py
    ├── registry.py
    └── validation.py
```

## Import Boundaries

```
ralph.mcp (public API via __init__.py)
├── ralph.mcp.artifacts.*         → artifacts/*
├── ralph.mcp.artifacts.bridge.*  → artifacts/bridge.py
├── ralph.mcp.protocol.*          → protocol/*
├── ralph.mcp.server.*            → server/*
├── ralph.mcp.tools.*             → tools/*

tools/ (Ralph as server)
└── tools/bridge.py
    ├── tools/names.py
    ├── protocol/capability_mapping.py

upstream/ (Ralph as client)
├── upstream/client.py
├── upstream/config.py
├── upstream/registry.py
└── upstream/validation.py
```
