# MCP Architecture

This page documents how ralph-workflow exposes itself as an MCP server and how the transport layer is structured.


> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first — it introduces MCP in context before these internals.

This page explains how Ralph Workflow's local MCP server is put together, how it decides which tools an agent may use, and how it proxies tools from upstream MCP servers.

## Overview

Ralph Workflow runs a local MCP (Model Context Protocol) server for each agent invocation. That server exposes the tools the agent can use during its session — workspace reads and writes, artifact submission, coordination, web search, bounded command execution, and more. Each tool call is filtered through a capability model derived from the active session drain.

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

Before any MCP tool call runs, Ralph Workflow checks it against the current session's capability set. The capability vocabulary lives in `ralph.mcp.protocol.capability_mapping`:

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

Ralph-managed same-workspace parallel workers are dormant in the bundled default (see [Parallel Mode](advanced-pipeline-configuration.md#parallel-execution-agent-driven)). This section documents the opt-in contract for the `ralph_fan_out` dispatch mode.

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

`ralph.mcp.upstream` implements a transparent proxy that forwards selected tool calls to one or more upstream MCP servers configured by the operator. This lets agents use tools from external MCP servers — for example a filesystem or search server — without Ralph Workflow having to implement every tool itself.

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

### `mcp__<server>__<tool>` alias exposure

The MCP server exposes every registered tool under **two** names in
`tools/list` so strict-MCP clients (e.g. Claude Code in strict MCP mode)
can always invoke the tool by the canonical Claude alias:

1. The **raw name** (e.g. `read_file`) — for backward compatibility
   with non-strict-MCP clients and direct dispatch paths.
2. The **Claude alias** (e.g. `mcp__ralph__read_file`) — what
   strict-MCP Claude Code actually invokes.

The rule (in `ralph.mcp.server._mcp_server.McpServer._handle_tools_list`):

- For each tool definition in the registry, emit the raw entry
  **unconditionally**.
- If `claude_tool_name(tool_name) != tool_name` (the alias is
  non-degenerate), emit a SECOND entry under the alias name with the
  same `description` and `inputSchema`.
- A runtime invariant enforces no duplicate `name` values in the
  returned `tools` list.
- An import-time invariant in the same module asserts that every
  member of `RalphToolName` produces a non-degenerate alias (i.e.
  `claude_tool_name(name) != name`).

The `tools/call` handler resolves BOTH `read_file` and
`mcp__ralph__read_file` to the same registered handler. The
`resolve_alias_to_canonical(name)` helper strips the
`mcp__<server>__` prefix when it matches the configured server name
(`ralph.mcp.tools.names.RALPH_MCP_SERVER_NAME = "ralph"`), so
strict-MCP clients can call `mcp__ralph__read_file` and have it
dispatch to the same handler as `read_file`.

The preflight (`ralph.mcp.protocol.startup.preflight_http_mcp_server_tools`)
accepts EITHER the raw name OR the alias name in `required_tools`, so
the preflight does not depend on the strict-MCP client's view of the
tool list.

If a tool is registered with a name that already starts with
`mcp__<server>__`, the server emits it ONCE under the alias name
(deduped); the raw entry is skipped to avoid the
`mcp__ralph__mcp__ralph__<tool>` double-prefix regression that
affected `ralph.agents.invoke._provider_allowed_mcp_tool_names`
before the fix.

## Protocol modules

| Module | Purpose |
|---|---|
| `ralph.mcp.protocol.session` | Session context passed to tool handlers |
| `ralph.mcp.protocol.startup` | Startup negotiation and handshake |
| `ralph.mcp.protocol.transport` | Transport type selection (stdio / SSE) |
| `ralph.mcp.protocol.env` | Environment variable injection into MCP sessions |

## Property test matrix

The MCP server's target architecture (A–N) is pinned by 13 black-box
property tests in `ralph-workflow/tests/test_property_*.py`. Each
test exercises the production transport through the in-memory harness
with no real time, no real sockets, and no real subprocess; the
contract is asserted by observable behavior on the shipped path.

| Property | Test file | Proof obligation (PROMPT.md Foundations) |
|---|---|---|
| A | `test_property_a_one_transport_one_behavior.py` | an audit/test confirms tool dispatch, streaming, session handling, concurrency control, and error framing are reachable only through the production transport; no alternate path carries them. |
| B | `test_property_b_session_contract_conformance.py` | both session implementations are checked for conformance against the session contract, so a member added to one and not the other fails; and an audit confirms no cast() sits at the session factory boundary (the specific laundering that hid the storm), so the type checker cannot be told to look away there. |
| C | `test_property_c_liveness_contract.py` | the documented liveness endpoint exists and responds; it reports unhealthy for an injected wedged server and healthy for a serving one; and recovery fires within the configured latency bound on an injected clock. |
| D | `test_property_d_failure_observability.py` | an injected post-header failure produces the structured record and increments the counter; startup emits the configuration banner. |
| E | `test_property_e_streaming_terminates.py` | the production transport, driven over in-memory buffers, terminates every committed response with a frame on every path - including injected exceptions and recovery-initiated shutdown. |
| F | `test_property_f_retry_side_effects.py` | a stream failed after partial execution surfaces the may-have-run-outcome-unknown classification, and a retry does not silently re-execute a side-effecting command. |
| G | `test_property_g_recovery_signal.py` | the watchdog ignores the agent own descendant processes when judging liveness, and the breaker trips on repeated identical failures fed from the transport layer - both on scripted signals with an injected clock and no real waiting. |
| H | `test_property_h_bounded_resources.py` | spawned servers are reaped (no orphans), concurrency saturation produces backpressure rather than silent queueing, and every cleanup loop terminates under an adversarial respawn fake. |
| I | `test_property_i_timing_safety.py` | a test asserts server worst-case resolution < client timeout by summing the real bounded constants for dispatch, drain, and kill escalation - an end-to-end ceiling computed from constants, not a measured run. |
| K | `test_property_k_trust_boundary.py` | the exec surface rejects connections outside its defined trust boundary and accepts those inside it, asserted over the in-memory transport. |
| L | `test_property_l_zero_progress_and_resume.py` | a pure guard, unit-tested, aborts after a fixed cap of consecutive identical failure signatures (and a fingerprint test confirms volatile tokens do not let a spiral evade the cap); a resume test confirms a retry continues rather than re-emitting the original task from scratch. |
| M | `test_property_m_structured_cause.py` | a watchdog-SIGTERM failure is classified by its preserved fire-reason, not relabeled by a stderr substring match, asserted on a fabricated failure whose text contains a misleading timeout token. |
| N | `test_property_n_spill_inside_workspace.py` | without an injected spill dir, oversized exec/unsafe_exec output spills to a path inside the workspace root (readable by the agent read tools), asserted over an in-memory workspace. |

A property is "done" only when the proof obligation
above is demonstrable as a fast, deterministic, black-box test that
asserts observable behavior on the shipped path.

## Related pages

- {doc}`mcp-tools` — full tool reference
- {doc}`artifacts` — the artifact submission tools
- {py:mod}`ralph.mcp` — full API reference
