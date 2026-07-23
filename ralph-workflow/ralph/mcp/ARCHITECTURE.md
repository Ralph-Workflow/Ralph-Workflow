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
| `bridge/` | `ToolBridge` registry, spec helpers, lazy handler dispatchers, `_specs_file_read/_file_write/_file_list/_git_exec/_web_media/_artifacts/_explore.py` MCP schemas; canonical re-export `ralph.mcp.tools.bridge.tool_specs` |
| `workspace/` | `handle_read_file`, `handle_read_multiple_files`, `handle_stat`, `handle_list_allowed_roots`, `handle_write_file`, `handle_list_directory`, `handle_search_files`, `handle_grep_files`, `handle_directory_tree`, `handle_edit_file`, `handle_append_file`, `handle_create_directory`, `handle_move_file`, `handle_copy_file`, `handle_delete_path`, `handle_read_media`, `handle_read_image`; sub-modules split by concern (`_read_handlers`, `_grep_handlers`, `_write_handlers`, `_list_ops`, `_media_handlers`, `_media_blocks`, `_media_io`, `_media_session`, `_utils`) |
| `git_read.py` | `handle_git_status`, `handle_git_diff`, `handle_git_log`, `handle_git_show`; `format='summary'` compact cards for git_log/git_show |
| `exec.py` / `_exec_run_deps.py` | `handle_exec_command`, `run_command`, `build_effective_exec_deps` — bounded subprocess execution routed through the reusable sandbox pool; dependency composition captures the thread-owned sink from `session.current_thread_tool_output_sink()` once at dispatch and wires it into `on_output_chunk` for SSE streaming; `format='summary'` returns a compact envelope plus replayable `ralph://exec/<spill-name>` handles |
| `unsafe_exec.py` | `handle_unsafe_exec_command`, `handle_raw_exec_command` — unrestricted shell command execution in the real workspace; intentionally kept without `format='summary'` so the legacy behavior stays unchanged (audited as `keep`) |
| `exec_sandbox.py` | `ExecSandboxManager` — lock-free bounded round-robin sandbox pool: per-workspace `_next_slot_index` counter selects slot `(counter % max_slots) + 1` without filesystem locks; `_active_slots` set prevents capacity recovery from deleting live slots; cleanup runs only when `base_dir > max_total_bytes` (capacity-gated, never under budget) |
| `md_artifact.py` | `handle_submit_md_artifact`, `handle_verify_md_artifact`, `handle_stage_md_artifact`, `handle_get_md_draft`, `handle_discard_md_draft`, `handle_finalize_md_artifact`, `handle_edit_md_plan_step` — the markdown artifact tool surface (canonical contract: `docs/agents/artifact-submission-contract.md`) |
| `artifact.py` | `ArtifactHandlerDeps`, `execute_ops_with_rollback` — shared persistence seam for the markdown handlers and canonical submission (JSON artifact authoring was removed) |
| `coordination.py` | `handle_report_progress`, `handle_declare_complete`, `handle_coordinate`, `handle_read_env` |
| `websearch.py` | `handle_web_search` with `format='summary'` compact envelopes and UTF-8-accurate `snippet_budget_bytes` |
| `webvisit.py` | `handle_visit_url` and `handle_download_url` with `format='metadata'` / `format='summary'` replayable resource handles |
| `_envelope_bytes.py` | `finalize_envelope_bytes_out` shared helper for self-referential `bytes_out` envelopes used by git_read, websearch, webvisit, and read_image/read_media |

**Canonical import path:** `from ralph.mcp.tools import ...` or `from ralph.mcp.tools.<module> import ...`

### `ralph.mcp.artifacts/` — Artifact Storage

Persistent artifact storage and per-type validators. Used by **both** Ralph's server-side tool handlers and the upstream client.

| File | Purpose |
|------|---------|
| `markdown/` | Closed markdown artifact grammar: `MdArtifactSpec`, `parse_and_validate`, `Diagnostic`, per-type specs under `markdown/specs/`, spec registry |
| `canonical_submit.py` | `submit_artifact_canonical` — the single canonical writer for `.agent/artifacts/<type>.md`, handoff copies, receipts, and sentinels |
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
| `runtime.py` | Server runtime (`build_standalone_http_server`) — single transport path |
| `factory.py` / `factory_impl.py` | Server factory |
| `lifecycle.py` | Server lifecycle; spawns the standalone process via `ProcessManager`; owns the `RestartAwareMcpBridge` restart policy |
| `__main__.py` | Entry point |

#### MCP server restart contract

`start_mcp_server(...)` returns a `RestartAwareMcpBridge` that wraps the live process and session.
The bridge reserves one localhost port at startup and reuses it on every restart so the
`MCP_ENDPOINT_ENV` value remains constant for the full lifetime of the bridge — agents that are
already executing never see a changed endpoint after a mid-run crash.

Active supervision runs via `McpSupervisor` (in `ralph.process.mcp_supervisor`) which polls
`check_mcp_bridge_health(bridge)` in a background thread for the duration of each agent attempt.

The bridge treats a server as **unhealthy** when either:

1. The subprocess has exited (`process.poll() is not None`), or
2. The subprocess is alive but the **responsiveness probe** fails — `probe_mcp_http_endpoint`
   (in `ralph.mcp.protocol.startup`) sends an isolated `initialize` → `notifications/initialized`
   → `tools/list` JSON-RPC handshake using a fresh, independent MCP session (never reusing the
   agent's active session) and raises `PreflightError` if the server does not respond within the
   probe timeout (default 5 s, configurable via `RALPH_MCP_PROBE_TIMEOUT_MS`).

On an unhealthy result, the bridge terminates the stale process via `StandaloneMcpProcess.shutdown()`,
respawns via `_spawn_mcp_process` (which re-runs full preflight), and increments the bounded
restart counter up to `McpRestartPolicy.max_restarts` (default: 20). Once the budget
is exhausted it raises `McpServerError` so the caller gets a crisp MCP-specific failure rather
than an opaque agent timeout.

All process spawning and termination during restart routes through `ProcessManager` as normal;
the bridge never holds a raw `Popen` handle outside that boundary.

**GET /health route (property C):** the production transport exposes a real `GET /health`
HTTP route on the same `_FallbackHttpHandler` that serves every other request. The route
invokes the existing `probe_mcp_http_endpoint` (via an injected `health_probe_fn`) and
returns 200 healthy / 503 unhealthy application/json. Saturation-rejected requests
(property H) return 503 + JSON-RPC -32001 `server saturated: try again later` from the
do_POST path; the transport-repetition breaker (property G) returns 503 + JSON-RPC -32001
`transport_loop_detected` when 3 identical failures within 60s are observed.

**Trust boundary (property K):** the bind host is hard-coded to `127.0.0.1` (never
`0.0.0.0`). When `MCP_AUTH_TOKEN` is set, the `Authorization: Bearer <token>` header is
verified via `hmac.compare_digest` before any further dispatch. An empty/unset
`MCP_AUTH_TOKEN` is treated as no-op (the loopback bind is the trust boundary).

**Startup banner:** the standalone server logs a single line on start that announces the
live configuration (transport, session class, dispatch cap, drain ceiling, kill escalation,
probe timeout, auth token set) so an operator can confirm what is running.

#### Target architecture properties (A–N)

The MCP server realizes the target architecture from `docs/PROMPT.md` (PROMPT.md).
Each property is paired with a fast, deterministic, black-box test in `tests/`,
and each row's Description column is the proof-obligation sentence from
PROMPT.md Foundations (verbatim) — the acceptance gate that the test
must demonstrably satisfy.

| Property | Test | Description |
|----------|------|-------------|
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
| Foundation | `test_in_memory_transport_round_trip.py` | In-memory transport harness — drives A, B, C, E, K |

Key guarantees:

- Both exited and alive-but-unresponsive MCP servers trigger the restart path.
- Restart is only reported successful after a full preflight re-validates endpoint tool reachability.
- The responsiveness probe uses an **isolated session** — it never touches the agent's active
  MCP session or mutates any shared server state.
- The bridge endpoint URI is **stable for the full bridge lifetime** — the port is reserved once
  and reused on every respawn; `bridge.agent_endpoint_uri()` never changes after the bridge starts.
- `check_mcp_bridge_health(bridge)` is a safe no-op on any non-`RestartAwareMcpBridge` object.
- When at least one restart occurs, the count is forwarded to `PipelineSubscriber.record_mcp_restart()`
  and surfaced as `mcp_restarts: <n>` in the run-end debug output. Active process labels from
  `ProcessManager.list_active()` are likewise included as `active_processes:` when non-empty.

**Canonical import path:** `from ralph.mcp.server import ...` or `from ralph.mcp.server.<module> import ...`

## Artifact Submission Error Contract

When markdown validation fails, `ralph_submit_md_artifact` / `ralph_verify_md_artifact` return a structured payload of line-anchored diagnostics — `{line, section, rule_id, message, severity}` — with `valid: false` and `is_error` set when any diagnostic has `error` severity. Nothing is persisted on error; the agent fixes the document the diagnostics point at and retries. `InvalidParamsError` is reserved for malformed tool calls (missing `artifact_type`/`content`, or an unregistered artifact type).

The bundled Markdown format references under `.agent/artifact-formats/<type>.md` are materialized into the workspace before each agent invocation (see `ralph/pipeline/session_bridge.py`), so agents can consult the expected document shape at any time.

## Capability System

### Ralph Capabilities

Ralph uses an internal capability vocabulary for session access control:

| Capability | Value | Description |
|------------|-------|-------------|
| `workspace.read` | `workspace.read` | Read files and list directories |
| `workspace.metadata_read` | `workspace.metadata_read` | Read file metadata/stat (distinct from content read) |
| `workspace.write_ephemeral` | `workspace.write_ephemeral` | Write to non-git-tracked files |
| `workspace.write_tracked` | `workspace.write_tracked` | Write to git-tracked files |
| `workspace.edit` | `workspace.edit` | Edit, append, create, move, copy files and directories |
| `workspace.delete` | `workspace.delete` | Delete files and directories (distinct destructive capability) |
| `process.exec_bounded` | `process.exec_bounded` | Execute bounded shell commands |
| `process.exec_unbounded` | `process.exec_unbounded` | Execute shell commands without limits |
| `artifact.submit` | `artifact.submit` | Submit structured artifacts |
| `run.report_progress` | `run.report_progress` | Report progress to pipeline |
| `git.status_read` | `git.status_read` | Read git status and history |
| `git.diff_read` | `git.diff_read` | Read git diffs |
| `git.write` | `git.write` | Perform git operations (orchestrator-only; never granted to agents) |
| `env.read` | `env.read` | Read environment variables |
| `env.write` | `env.write` | Write environment variables |
| `upstream.tool_use` | `upstream.tool_use` | Use upstream MCP tools |
| `web.search` | `web.search` | Search the web |
| `web.visit` | `web.visit` | Fetch and extract text from a URL (default-enabled; non-commit drains) |
| `media.read` | `media.read` | Read media files (images, PDFs, documents, audio, video) — default-on |

### MCP Capability Mapping

MCP capabilities are mapped to Ralph capabilities:

| MCP Capability | Ralph Capability |
|----------------|-------------------|
| `FileRead` | `workspace.read` |
| `WorkspaceRead` | `workspace.read` |
| `WorkspaceMetadataRead` | `workspace.metadata_read` |
| `WorkspaceWriteEphemeral` | `workspace.write_ephemeral` |
| `WorkspaceWriteTracked` | `workspace.write_tracked` |
| `WorkspaceEdit` | `workspace.edit` |
| `WorkspaceDelete` | `workspace.delete` |
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
| `WebVisit` | `web.visit` |
| `MediaRead` | `media.read` |

### Multimodal Capability (MediaRead)

The `MediaRead` capability gates access to the `read_media` and `read_image` tools. It is:

- **Default-on** via `media.enabled = true` (or omitted, as it is the default)
- **Can be disabled** via `media.enabled = false` in `mcp.toml`
- **Suppressed from clients** that don't declare multimodal support in `initialize`
- **Enforced at runtime** via session capability check

## Multimodal MCP Support

Ralph supports broad multimodal MCP tools as a default-on, capability-gated feature. `read_media` is the primary tool; `read_image` is a compatibility alias for inline-image workflows.

Supported modality classes: images (PNG, JPEG, GIF, WebP), PDFs, documents, audio, video, and resource/file-reference-based flows. Ralph automatically determines what the active provider/model supports and selects inline vs resource-reference delivery accordingly.

### Disabling Multimodal Support

To disable multimodal support:

```toml
[media]
enabled = false
```

To customize without disabling (or omit `enabled` as it defaults to true):

```toml
[media]
enabled = true
max_inline_bytes = 10485760  # 10 MiB to allow larger images
```

### Client Capability Filtering

When a client sends the MCP `initialize` request, Ralph captures declared capabilities from `params.capabilities`. The following signals indicate multimodal support:

- `capabilities.image` (any truthy value)
- `capabilities.media` (any truthy value)
- `capabilities.multimodal` (any truthy value)

If none are present, the client is treated as text-only.

When building `tools/list` responses, Ralph filters out tools marked `is_multimodal=True` for text-only clients. This ensures:

1. **Backward compatibility** — existing text-only clients never see multimodal tools
2. **Client-gated visibility** — multimodal tools only appear when the client declares support
3. **Consistent wire format** — text content blocks remain `{"type": "text", "text": ...}`

### Upstream Multimodal Normalization Policy

When an upstream MCP server returns a content block with `type != "text"`, Ralph normalizes it to a `resource_reference` rather than rejecting or silently dropping it:

- **URI-backed content**: the external URI is preserved as-is in a `resource_reference` block
- **Embedded-data content**: the bytes are stored in the session `MediaManifest` and a `resource_reference` block with a `ralph://media/...` URI is returned, making it retrievable via `resources/read`

This policy:
- Preserves multimodal meaning across upstream tool boundaries
- Makes multimodal content retrievable rather than discarded
- Maintains a clear boundary between supported delivery modes and genuinely unsupported block shapes

### Same-Workspace Parallel Worker Session Contract

Same-workspace parallel workers inherit the parent phase's `SessionMcpPlan` contract verbatim. The session contract includes the drain, capabilities, resolved `MultimodalModelIdentity`, and `ResolvedCapabilityProfile`. This ensures that parallel workers expose the same multimodal capability surface as serial execution:

- `read_media` and `read_image` are available by default when the parent phase has `media.read` capability
- Delivery verdicts (inline image, typed block, resource reference replay, explicit unsupported) are provider-specific and consistent with the serial path
- Worker-produced media artifacts are written under the worker's namespace with the phase-scoped handoff path, not a standalone fallback

The session contract is propagated via `SameWorkspaceContext` fields (`session_drain`, `session_capabilities`, `session_model_identity`, `session_capability_profile`) from the runner's `build_session_mcp_plan` call into `_fan_out_worker_context`, then into `build_worker_session` where it is used to construct the worker `AgentSession`.

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
| `handle_submit_md_artifact`, `handle_verify_md_artifact`, the draft staging handlers, `handle_edit_md_plan_step` | `from ralph.mcp.tools.md_artifact import ...` (canonical contract: `docs/agents/artifact-submission-contract.md`) |
| `handle_report_progress`, `handle_declare_complete`, etc. | `from ralph.mcp.tools.coordination import ...` |
| `handle_web_search` | `from ralph.mcp.tools.websearch import ...` |
| `handle_visit_url`, `handle_download_url` | `from ralph.mcp.tools.webvisit import ...` |
| `handle_unsafe_exec_command`, `handle_raw_exec_command` | `from ralph.mcp.tools.unsafe_exec import ...` |
| `finalize_envelope_bytes_out` | `from ralph.mcp.tools._envelope_bytes import ...` |
| `AUDIT_REGISTER`, `bench_results_to_measurements`, `refresh_audit_register` | `from ralph.mcp.explore.audit_register import ...` |
| `ralph_index_status`, `ralph_reindex`, `ralph_graph` handlers | `from ralph.mcp.explore.handlers import ...` |
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
├── explore/             # Phase 0-4 indexed exploration substrate (SQLite + FTS5 + graph)
│   ├── __init__.py
│   ├── audit_register.py  # Per-tool outcome register (keep/add_argument/rework_internals/defer)
│   ├── bench.py          # Scripted-flow benchmark harness, no LLM, deterministic Clock seam
│   ├── dirty_paths.py    # Persisted queue + mark_dirty seam for write handlers
│   ├── graph.py          # Bounded recursive-CTE graph queries for ralph_graph
│   ├── handlers.py       # ralph_index_status / ralph_reindex / ralph_graph MCP handlers
│   ├── lifecycle.py      # Before/after dev-fix session refresh hooks
│   ├── pipeline.py       # Manifest/hash/generation lifecycle, idempotent reindex
│   ├── ranking.py        # Deterministic score components for search_files and grep_files
│   ├── store.py          # SQLite + FTS5 schema, manifest, evidence, tombstones, dirty_paths, jobs, settings
│   ├── structure.py      # Python AST and Markdown heading/link extractors
│   └── deferred_phases.py
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
│   ├── _envelope_bytes.py  # Shared self-referential bytes_out helper
│   ├── artifact.py
│   ├── bridge/             # ToolBridge registry, lazy dispatchers, spec helpers
│   │   ├── __init__.py
│   │   ├── _registry.py
│   │   ├── _tool_bridge.py
│   │   ├── _specs_file_read.py
│   │   ├── _specs_file_write.py
│   │   ├── _specs_file_list.py
│   │   ├── _specs_git_exec.py
│   │   ├── _specs_web_media.py
│   │   ├── _specs_artifacts.py
│   │   └── _specs_explore.py
│   ├── coordination.py
│   ├── exec.py
│   ├── git_read.py
│   ├── names.py
│   ├── unsafe_exec.py
│   ├── websearch.py
│   ├── webvisit.py        # handle_visit_url, handle_download_url
│   └── workspace/         # handle_read_file, handle_write_file, etc., split into:
│       ├── __init__.py
│       ├── _read_handlers.py
│       ├── _grep_handlers.py
│       ├── _write_handlers.py
│       ├── _list_ops.py
│       ├── _media_handlers.py
│       ├── _media_blocks.py
│       ├── _media_io.py
│       ├── _media_session.py
│       └── _utils.py
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

- **Upstream multimodal content handling** — Previously would have silently stringified non-text blocks. Now normalized to `resource_reference` artifacts (embedded-data content stored in the session manifest; URI-backed content preserved as external reference) rather than rejected or dropped.
- **MediaRead capability** — Was defined but not wired to any tool. Now integrated with `read_image` tool registration and client capability filtering.
- **Client capability extraction** — Was not implemented. Now captures client `capabilities` from MCP `initialize` handshake and uses it to filter multimodal tools.

The MCP subsystem now has zero dormant paths: every defined capability is either used by a registered tool or explicitly documented as not applicable to the maintained implementation.
