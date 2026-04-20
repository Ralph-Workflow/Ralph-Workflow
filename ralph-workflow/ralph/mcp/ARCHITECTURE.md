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

**Public API (re-exports via flat `upstream_*.py` stubs):** `HttpUpstreamClient`, `StdioUpstreamClient`, `UpstreamMcpServer`, `UpstreamTool`, `UpstreamCallError`, `UpstreamRegistry`, `validate_upstream_mcp_servers`, etc.

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

**Public API (re-exports via flat `tool_*.py` stubs):** `ToolBridge`, `ToolDefinition`, `ToolMetadata`, `build_ralph_tool_registry`, and all `handle_*` functions.

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

**Public API (re-exports via flat `artifacts.py`, `bridge.py`, etc.):** All store functions, `MCPBridge`, `BridgeConfig`.

### `ralph.mcp.protocol/` — Shared Protocol Plumbing

Used by **both** the MCP server (Ralph → agents) and upstream client (Ralph → external MCPs).

| File | Purpose |
|------|---------|
| `transport.py` | `MCPTransport`, `StdioTransport`, `MCPMessage`, `TransportError` |
| `session.py` | `AgentSession`, `session_has_capability` |
| `capability_mapping.py` | `Capability`, `McpCapability`, `AccessMode`, policy mapping functions |
| `env.py` | `McpEnvVar`, environment variable constants |
| `startup.py` | Preflight helpers, `HeartbeatPolicy`, `access_mode_for_drain` |

**Public API (re-exports via flat `transport.py`, `startup.py`, etc.):** `MCPTransport`, `StdioTransport`, `HeartbeatPolicy`, `access_mode_for_drain`, etc.

### `ralph.mcp.server/` — Standalone MCP Server

The standalone `ralph-mcp` runtime (not changed in this reorganization).

| File | Purpose |
|------|---------|
| `runtime.py` | Server runtime |
| `factory.py` / `factory_impl.py` | Server factory |
| `lifecycle.py` | Server lifecycle |
| `__main__.py` | Entry point |

## Import Boundaries

```
ralph.mcp.__init__ (public API)
├── ralph.mcp.artifacts          → artifacts/__init__.py
│   └── artifacts/store.py, artifacts/file_backend.py, ...
├── ralph.mcp.artifacts.bridge  → artifacts/bridge.py
├── ralph.mcp.protocol.startup  → protocol/startup.py
│   └── (imports tools.bridge at runtime via build_ralph_tool_registry)
├── ralph.mcp.protocol.transport → protocol/transport.py
├── ralph.mcp.tools.bridge      → tools/bridge.py (lazy via __getattr__)
│   └── tools/names.py, protocol/capability_mapping.py, upstream/registry

upstream/ (Ralph as client)
├── upstream/client.py
├── upstream/config.py
├── upstream/registry.py        → (imports upstream/validation.py at runtime)
└── upstream/validation.py

tools/ (Ralph as server)
└── tools/bridge.py
    ├── tools/names.py
    ├── protocol/capability_mapping.py
    └── upstream/registry.py (TYPE_CHECKING only)
```

## Backward Compatibility

Old flat import paths continue to work via stub files that re-export from the new sub-packages:

| Old import | New import |
|------------|------------|
| `ralph.mcp.transport` | `ralph.mcp.protocol.transport` |
| `ralph.mcp.startup` | `ralph.mcp.protocol.startup` |
| `ralph.mcp.tool_bridge` | `ralph.mcp.tools.bridge` |
| `ralph.mcp.artifacts` | `ralph.mcp.artifacts.store` |
| `ralph.mcp.bridge` | `ralph.mcp.artifacts.bridge` |
| `ralph.mcp.upstream_client` | `ralph.mcp.upstream.client` |
| `ralph.mcp.upstream_config` | `ralph.mcp.upstream.config` |
| ... | ... |

The flat stubs (`transport.py`, `startup.py`, `tool_bridge.py`, etc.) in `ralph/mcp/` are thin re-export layers. The canonical location for new code is the sub-packages.
