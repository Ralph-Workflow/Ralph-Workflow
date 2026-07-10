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

## Indexed exploration substrate

Ralph Workflow maintains a deterministic, disposable SQLite+FTS5 indexed
exploration substrate under `.agent/ralph-explore/` for the current
workspace. The substrate is owned by `ralph.mcp.explore` and exposes three
new MCP tools:

* `ralph_index_status` — reports generation, freshness, dirty paths, job history, storage bytes, and gitignore coverage. Side-effect free: it inspects the existing on-disk state and never creates SQLite files or `.agent/ralph-explore/` directories. The response carries `managed_ignore_rule_repair`, a structured next-run seeding instruction surfaced when the managed `.agent/` gitignore rule is absent so callers can repair the disposable-cache coverage without guessing.
* `ralph_reindex` — runs a bounded `changed`/`full` refresh (timeout-based, fail-closed for the job, fail-open for the agent). `timeout_ms` is bounded in `[1, 60000]`; out-of-range or malformed values are rejected before any I/O. Returns `job_id`, `job_status`, `generation`, `changed_files`, `failed_files`, `parse_count`, `dirty_paths_count`, `elapsed_seconds`, `error_summary`.
* `ralph_graph` — graph-native queries (`neighbors`, `path`, `impact`, `hubs`, `tests`) with bounded traversal, evidence-backed output, and `freshness` policy (`required` / `prefer_fresh` / `allow_stale`). The query path is bounded by a per-call `timeout_ms` (1-30000) and an explicit `cancel` flag; deadline expiry and cancellation return a bounded, truthful incomplete result (`deadline_exceeded=true` / `cancelled=true` plus `missing_data`) without exposing mutable work to readers.

Existing read/search tools gain optional indexed arguments (`use_index`,
`evidence_id`, `span_id`, `symbol`, `rank_by`, `return_evidence_ids`,
`ranked`, `role`, etc.) so agents can choose indexed or live behavior per
call. Indexed responses carry `index_used`, `index_generation`, `is_stale`,
`stale_paths_count`, `dirty_paths_count`, and `fallback_reason` so callers
can detect fall-back without inspecting tool internals.

The substrate is:

* **deterministic** — no LLM, no embedding, no network call. The index can be deleted and rebuilt without affecting source files or workflow artifacts.
* **git-ignored** — `auto_seed_default_gitignore` in `ralph/config/bootstrap.py` appends both the parent `.agent/` rule and the explicit `.agent/ralph-explore/` child rule so the disposable cache coverage is reported transparently in the seeded default gitignore.
* **bounded** — job history caps at 100/14 days; evidence tombstones cap at 10k/30 days; SQLite WAL mode + busy-timeout guarantee concurrent readers.
* **fail-open for the agent** — bounded reindex runs before and after every development/fix agent invocation; timeout never blocks the agent.
* **idempotent** — running reindex twice against the same tree produces the same logical rows (generation metadata aside); unchanged files are detected by content hash and skipped.

### Module layout

The substrate is split into focused submodules under `ralph.mcp.explore`:

* `store` — SQLite + FTS5 DDL, manifest, evidence, tombstones, dirty_paths, jobs, settings, plus the structure tables (`spans`, `symbols`, `edges`).
* `pipeline` — manifest/hash/generation lifecycle, idempotent reindex, single-writer coalescing.
* `dirty_paths` — persisted queue + `mark_dirty` seam for write handlers.
* `ranking` — deterministic score components (lexical, symbol, graph, changed) for `search_files` and `grep_files`.
* `structure` — Python AST and Markdown heading/link/anchor extractors (Phase 2).
* `graph` — bounded recursive-CTE graph queries for `ralph_graph` (Phase 2).
* `handlers` — `ralph_index_status`, `ralph_reindex`, and `ralph_graph` MCP handlers.
* `bench` — scripted-flow benchmark harness (no LLM, deterministic Clock seam).
* `audit_register` — Phase 0 per-tool outcome register (keep / add_argument / rework_internals / defer).
* `deferred_phases` — tracked deferral register for remaining optional work.
* `lifecycle` — before/after dev-fix session refresh hooks used by the pipeline runner.

### Phase scope

* **Phase 1 (lexical, shipped)** — FTS5 chunking, content-hash evidence, idempotent reindex, mutation dirty marking. The `.agent/ralph-explore/` index lives under a parent `.agent/` plus an explicit `.agent/ralph-explore/` child rule in the bootstrap-managed gitignore so the disposable cache coverage is reported transparently.
* **Phase 2 (structure + graph, shipped)** — Python AST and Markdown structure extraction in `structure.py`, the `spans`/`symbols`/`edges` SQLite tables, and `ralph_graph` for callers/path/impact/hubs/tests queries. The structure and graph tables also feed `+100 / +80 / +60 / +50 / +40 / +30 / +20 / -50` score components for `search_files` and `grep_files`; when the index lacks the corresponding data, those components report `+0 component:no_indexed_data` with an explicit zero-applied reason rather than `disabled:phase2`. See `ralph-workflow/ralph/mcp/explore/structure.py` for the Python AST + Markdown extractors, `ralph-workflow/ralph/mcp/explore/graph.py` for the `ralph_graph` neighbors/path/impact/hubs/tests queries, and the `ralph-workflow/ralph/mcp/explore/ranking.py` score-component ladder.
* **Phase 3 (impact-aware editing, shipped)** — `edit_file` accepts `expected_content_hash`, `target` (`evidence_id` / `span_id` / `symbol`), `match_strategy` (`exact` / `within_target` / `all_in_target`), `reindex` (`auto` / `skip` / `changed_blocking`), `impact_preview`, and `return_evidence_updates`. `impact_preview` runs a conservative `ralph_graph` impact query when the index has the target symbol. Phase 3 wiring lives in `ralph-workflow/ralph/mcp/tools/bridge/_specs_file_write.py` (edit_file MCP schema for `match_strategy`/`target`/`expected_content_hash`/`impact_preview`/`reindex`/`return_evidence_updates`) and `ralph-workflow/ralph/mcp/tools/workspace/_write_handlers.py:handle_edit_file` (the spec enforcement), with the conservative `rename`/`signature`/`behavior`/`delete`/`unknown` labels defined in `ralph-workflow/ralph/mcp/explore/graph.py:_IMPACT_RELATIONS`. Malformed Python extraction raises a typed `PythonExtractionError` that the reindex pipeline catches in its preflight so lexical/structure rows for the path remain queryable while the path is reported in `failed_files` and retried on the next pass.
* **Phase 4 (audited non-index remediation, shipped)** — `git_status` (`format=compact`), `git_diff` (`format=summary`), and `exec` (`format=summary`) are shipped with the compact/summary output modes; `unsafe_exec` and its `raw_exec` alias are kept unchanged (`keep`) because the summary mode is intentionally only on the bounded exec path. The remaining non-index MCP families — `git_log`, `git_show`, the artifact submission tools, the planning tools, the coordination tools, the web tools, and the media tools — are audited as `keep` in `audit_register` with baseline counters and rationale. `exec` summary mode returns `stdout_resource_id` and `stderr_resource_id` handles of the form `ralph://exec/<spill-name>`; production sessions attach an `ExecResourceResolver` in `ralph.mcp.tools._exec_resource_uri` so those handles are replayable through `resources/read` (the resource template `ralph://exec/{spill_name}` is registered alongside `ralph://media/{artifact_id}`). Sessions without the resolver return a structured "resolver not attached" error so legacy clients get a consistent failure mode while the raw output remains available.
* **Optional deferred work** — `phase_5` is the only entry in `ralph.mcp.explore.deferred_phases`. It tracks the `ralph_explore` wrapper, NetworkX offline metrics, Kuzu adapters, and additional Tree-sitter parsers, all gated on measured SQLite bottleneck evidence and never shipped by default.
