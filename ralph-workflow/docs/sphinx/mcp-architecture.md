# MCP Architecture

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it introduces MCP in context before these internals.

How the Ralph Workflow MCP server is structured, how it controls agent access, and how it proxies upstream tools.

## Overview

Ralph Workflow runs a local MCP (Model Context Protocol) server for each agent invocation. The server exposes a set of tools — workspace read/write, artifact submission, coordination, web search, process execution, and more — that the agent can call during its session. Access to each tool is gated by a capability model derived from the active session drain.

```
Agent subprocess
    │
    │  MCP (stdio or SSE)
    ▼
Ralph Workflow MCP server  (ralph.mcp.server)
    │
    ├── Capability gate  (ralph.mcp.protocol.capability_mapping)
    ├── Tool dispatch    (ralph.mcp.tools.*)
    │
    └── Upstream proxy  (ralph.mcp.upstream)
            │
            └── Upstream MCP servers (e.g. filesystem MCP, search MCP)
```

## Capability model

Every MCP tool call is evaluated against the session's capability set before execution. Capabilities are defined in `ralph.mcp.protocol.capability_mapping`:

- `Capability` — internal Ralph Workflow capability vocabulary (e.g. `workspace.read`, `artifact.submit`)
- `McpCapability` — typed MCP-level capability vocabulary (e.g. `WorkspaceRead`, `ArtifactSubmit`)
- `SessionDrain` — the pipeline drain that determines the capability defaults
- `DrainClass` — coarser grouping (planning, development, analysis, review, fix, commit)

The `check_mcp_capability_policy` function is the single entry point for access decisions. Development and fix drains get read-write workspace access; all other drains are read-only.

Capability grants in a session are declared in `ralph.mcp.session_plan` and are injected into the MCP server at startup via the session context.

Key capability classes in the extended vocabulary:

| Capability | Description |
|---|---|
| `workspace.read` | Read files and list directories |
| `workspace.metadata_read` | Read file metadata/stat without reading content |
| `workspace.write_ephemeral` | Write to non-git-tracked files |
| `workspace.write_tracked` | Write to git-tracked files |
| `workspace.edit` | Edit, append, create, move, and copy files |
| `workspace.delete` | Delete files and directories (distinct destructive capability) |
| `web.visit` | Fetch and extract text from a URL (opt-in; non-commit drains) |
| `git.write` | Perform git write operations — **orchestrator-only; never granted to agents** |

Commit drains are strictly read-only: they receive only base read capabilities plus `run.report_progress`. They do not receive `git.write`, `workspace.write_ephemeral`, `workspace.write_tracked`, `workspace.edit`, or `process.exec_bounded`. The orchestrator is solely responsible for performing the actual git write operation after a commit agent proposes a commit message via `artifact.submit`.

## Session plan

`ralph.mcp.session_plan` constructs the capability grant set for a given drain and policy configuration. It resolves which capabilities the agent receives, validates that required capabilities are present, and produces the `SessionPlan` object consumed by the server factory.

### Same-workspace parallel worker session contract

Same-workspace parallel workers inherit the parent phase's session contract verbatim. The contract includes the drain, capabilities, resolved `MultimodalModelIdentity`, and `ResolvedCapabilityProfile`. This ensures that parallel workers expose the same multimodal capability surface as serial execution:

- `read_media` and `read_image` are available by default when the parent phase has `media.read` capability
- Delivery verdicts (inline image, typed block, resource reference replay, explicit unsupported) are provider-specific and consistent with the serial path
- Worker-produced media artifacts are written under the worker's namespace with the phase-scoped handoff path, not a standalone fallback

The session contract is propagated via `SameWorkspaceContext` fields (`session_drain`, `session_capabilities`, `session_model_identity`, `session_capability_profile`) from the runner's `build_session_mcp_plan` call into `_fan_out_worker_context`, then into `build_worker_session` where it constructs the worker `AgentSession`.

## Server lifecycle

The MCP server lifecycle is managed by three modules:

| Module | Responsibility |
|---|---|
| `ralph.mcp.server.lifecycle` | Start, stop, and health-check the server process |
| `ralph.mcp.server.factory` | Public factory interface — `create_server(session_plan)` |
| `ralph.mcp.server.factory_impl` | Concrete factory implementation; wires tools to the capability gate |
| `ralph.mcp.server.runtime` | Runtime context shared across tool handlers during a session |

The server is started before the agent subprocess is spawned and stopped after the agent exits.

## Standalone entry point

`ralph.mcp.server.__main__` provides a standalone `ralph-mcp` entry point that starts the MCP server outside of a full pipeline run. This is useful for debugging tool calls or connecting an agent manually during development.

```bash
python -m ralph.mcp.server --drain development --workspace .
```

## Upstream MCP proxy

`ralph.mcp.upstream` implements a transparent proxy that forwards selected tool calls to one or more upstream MCP servers configured by the operator. This allows agents to use tools provided by external MCP servers (e.g. a filesystem MCP, a search MCP) without Ralph Workflow having to implement each tool natively.

Key submodules:

| Module | Purpose |
|---|---|
| `ralph.mcp.upstream.client` | Low-level MCP client that connects to an upstream server |
| `ralph.mcp.upstream.registry` | Manages the set of active upstream connections |
| `ralph.mcp.upstream.agent_probe` | Probes whether an upstream MCP server is reachable |
| `ralph.mcp.upstream.config` | Configuration models for upstream server entries |
| `ralph.mcp.upstream.models` | Shared data models |
| `ralph.mcp.upstream.validation` | Validates upstream server responses |

## MCP tools

The full tool list is in {doc}`mcp-tools`. Tools are implemented in `ralph.mcp.tools.*`:

| Package | Tools provided |
|---|---|
| `ralph.mcp.tools.artifact` | `ralph_submit_artifact`, plan section tools |
| `ralph.mcp.tools.workspace` | `read_file`, `read_multiple_files`, `stat_path`, `list_allowed_roots`, `write_file`, `list_directory`, `search_files`, `grep_files`, `edit_file`, `append_file`, `create_directory`, `move_file`, `copy_file`, `delete_path`, `directory_tree`, `list_directory_recursive`, `read_media`, `read_image` |
| `ralph.mcp.tools.exec` | `exec` (bounded shell execution) |
| `ralph.mcp.tools.git_read` | `git_status`, `git_diff`, `git_log`, `git_show` |
| `ralph.mcp.tools.websearch` | `web_search` |
| `ralph.mcp.tools.webvisit` | `visit_url` |
| `ralph.mcp.tools.coordination` | `coordinate` (parallel work unit coordination) |
| `ralph.mcp.tools.bridge` | `report_progress`, `read_env`, `declare_complete` |

Tool names are defined in `ralph.mcp.tools.names`.

## Protocol modules

| Module | Purpose |
|---|---|
| `ralph.mcp.protocol.session` | Session context passed to tool handlers |
| `ralph.mcp.protocol.startup` | Startup negotiation and handshake |
| `ralph.mcp.protocol.transport` | Transport type selection (stdio / SSE) |
| `ralph.mcp.protocol.env` | Environment variable injection into MCP sessions |

## Related pages

- {doc}`mcp-tools` — full tool reference
- {doc}`artifacts` — the artifact submission tools
- {py:mod}`ralph.mcp` — full API reference
