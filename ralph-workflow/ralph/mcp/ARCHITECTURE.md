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
| `workspace.py` | `handle_read_file`, `handle_write_file`, `handle_list_directory`, `handle_read_image`, etc. |
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
| `format_docs/` | Package of bundled dumb-proof Markdown reference docs — `load_bundled_format_doc`, `materialize_format_doc`, `FORMAT_DOC_ARTIFACT_TYPES`; one `.md` per non-plan artifact type loaded via `importlib.resources` |
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

## Artifact Submission Error Contract

For every non-plan artifact type, when validation fails Ralph materializes a Markdown reference into `.agent/artifact-formats/<type>.md` and the `InvalidParamsError` message points the agent at that file instead of returning raw validator text. Agents must re-read the reference file before retrying.

Plan submission errors are exempt: per-section planning validation already surfaces section-specific, executor-ready messages via `handle_submit_plan_section`/`handle_finalize_plan`.

## Capability System

### Ralph Capabilities

Ralph uses an internal capability vocabulary for session access control:

| Capability | Value | Description |
|------------|-------|-------------|
| `workspace.read` | `workspace.read` | Read files and list directories |
| `workspace.write_ephemeral` | `workspace.write_ephemeral` | Write to non-git-tracked files |
| `workspace.write_tracked` | `workspace.write_tracked` | Write to git-tracked files |
| `process.exec_bounded` | `process.exec_bounded` | Execute bounded shell commands |
| `process.exec_unbounded` | `process.exec_unbounded` | Execute shell commands without limits |
| `artifact.submit` | `artifact.submit` | Submit structured artifacts |
| `run.report_progress` | `run.report_progress` | Report progress to pipeline |
| `git.status_read` | `git.status_read` | Read git status and history |
| `git.diff_read` | `git.diff_read` | Read git diffs |
| `git.write` | `git.write` | Perform git operations |
| `env.read` | `env.read` | Read environment variables |
| `env.write` | `env.write` | Write environment variables |
| `upstream.tool_use` | `upstream.tool_use` | Use upstream MCP tools |
| `web.search` | `web.search` | Search the web |
| `media.read` | `media.read` | Read image files (opt-in) |

### MCP Capability Mapping

MCP capabilities are mapped to Ralph capabilities:

| MCP Capability | Ralph Capability |
|----------------|-------------------|
| `FileRead` | `workspace.read` |
| `WorkspaceRead` | `workspace.read` |
| `WorkspaceWriteEphemeral` | `workspace.write_ephemeral` |
| `WorkspaceWriteTracked` | `workspace.write_tracked` |
| `WorkspaceWriteAny` | Composite (ephemeral OR tracked) |
| `GitStatusRead` | `git.status_read` |
| `GitRead` | `git.status_read` |
| `GitWrite` | `git.write` |
| `ProcessExec` | `process.exec_bounded` |
| `ProcessExecBounded` | `process.exec_bounded` |
| `ProcessExecUnbounded` | `process.exec_unbounded` |
| `ArtifactSubmit` | `artifact.submit` |
| `EnvRead` | `env.read` |
| `EnvWrite` | `env.write` |
| `UpstreamToolUse` | `upstream.tool_use` |
| `WebSearch` | `web.search` |
| `MediaRead` | `media.read` |

### Multimodal Capability (MediaRead)

The `MediaRead` capability gates access to the `read_image` tool. It is:

- **Opt-in** via `media.enabled = true` in `mcp.toml`
- **Suppressed from clients** that don't declare multimodal support in `initialize`
- **Enforced at runtime** via session capability check

## Multimodal MCP Support

Ralph supports image-reading MCP tools as an opt-in, capability-gated feature.

### Enabling Multimodal Support

```toml
[media]
enabled = true
max_inline_bytes = 5242880  # 5 MiB default
```

### Client Capability Filtering

When a client sends the MCP `initialize` request, Ralph captures declared capabilities from `params.capabilities`. The following signals indicate multimodal support:

- `capabilities.image` (any truthy value)
- `capabilities.media` (any truthy value)
- `capabilities.multimodal` (any truthy value)

If none are present, the client is treated as text-only.

When building `tools/list` responses, Ralph filters out tools marked `is_multimodal=True` for text-only clients. This ensures:

1. **Backward compatibility** — existing text-only clients never see multimodal tools
2. **Opt-in visibility** — multimodal tools only appear when the client declares support
3. **Consistent wire format** — text content blocks remain `{"type": "text", "text": ...}`

### Upstream Multimodal Rejection Policy

When an upstream MCP server returns a content block with `type != "text"`, Ralph rejects it with a clear error rather than silently stringifying or dropping the block.

Error format:
```
upstream server '<name>' tool '<tool>' returned multimodal content block (type='<type>') 
which is not supported in Ralph's text-only passthrough at index <idx>. 
Upstream multimodal payloads must be rejected rather than passed through.
```

This policy:
- Prevents silent data loss in text-only downstream flows
- Makes incompatibility visible rather than隐性
- Maintains a clear boundary between supported and unsupported upstream features

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
| `FORMAT_DOC_ARTIFACT_TYPES`, `materialize_format_doc`, etc. | `from ralph.mcp.artifacts.format_docs import ...` |

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
│   ├── format_docs/     # Package: bundled Markdown reference docs (one per artifact type)
│   │   ├── __init__.py  # Public API: FORMAT_DOC_ARTIFACT_TYPES, materialize_format_doc, etc.
│   │   ├── commit_message.md
│   │   ├── development_result.md
│   │   ├── issues.md
│   │   ├── fix_result.md
│   │   ├── development_analysis_decision.md
│   │   └── review_analysis_decision.md
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

## Dead Code Cleanup Outcome

During the multimodal MCP implementation, the following previously-dormant MCP paths were evaluated:

- **Upstream multimodal content handling** — Previously would have silently stringified non-text blocks. Now explicitly rejected with clear error message. No dead code removed; behavior corrected.
- **MediaRead capability** — Was defined but not wired to any tool. Now integrated with `read_image` tool registration and client capability filtering.
- **Client capability extraction** — Was not implemented. Now captures client `capabilities` from MCP `initialize` handshake and uses it to filter multimodal tools.

The MCP subsystem now has zero dormant paths: every defined capability is either used by a registered tool or explicitly documented as not applicable to the maintained implementation.
