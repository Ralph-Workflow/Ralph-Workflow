# MCP Tools Reference

This page documents the MCP tools that ralph-workflow exposes and the timeout contract every tool call must satisfy.


Ralph Workflow runs a private MCP (Model Context Protocol) server for each agent invocation. Agents connect to it automatically; you do not need to wire it up by hand. The server exposes workspace access, artifact submission, coordination, and web tools, all gated by the capability set for the current session drain.

## Native Tools

The following tools are exposed directly by Ralph Workflow's MCP server. The
capability gate column lists the capability that must be present in the session for
the tool to be callable.

The table below uses a few drain groupings:

- **all** — every drain: planning, development, development\_analysis, development\_commit,
  review, review\_analysis, review\_commit, fix, commit
- **write drains** — development, fix only
- **commit drains** — development\_commit, review\_commit, commit
- **non-analysis/commit** — planning, development, review, fix (web.search opt-in)

| Tool name | Capability gate | Granted to drains | Brief description |
|-----------|----------------|-------------------|-------------------|
| `read_file` | `workspace.read` | all | Read a UTF-8 file, with optional partial read params (line_start/line_end, head, tail, offset/limit). Returns structured JSON when partial params are used. |
| `read_multiple_files` | `workspace.read` | all | Read multiple files in one call, per-file success/failure |
| `stat_path` | `workspace.metadata_read` | all | Get file metadata/stat (type, size, created, modified, mode) |
| `list_allowed_roots` | `workspace.read` | all | Return configured allowed workspace root paths |
| `write_file` | `workspace.write_tracked` | write drains | Write or overwrite a tracked file |
| `list_directory` | `workspace.read` | all | List entries in a directory |
| `list_directory_recursive` | `workspace.read` | all | Recursive directory listing (text dump) |
| `directory_tree` | `workspace.read` | all | Structured JSON tree with max_depth and exclude_patterns support |
| `search_files` | `workspace.read` | all | True glob-pattern file search (**, *, ?, exclude, limit) |
| `grep_files` | `workspace.read` | all | Native content search (regex/literal, case/whole-word, context lines, include/exclude) |
| `edit_file` | `workspace.edit` | write drains | Structured edit with dry_run preview and unified diff |
| `append_file` | `workspace.edit` | write drains | Append content to a file |
| `create_directory` | `workspace.edit` | write drains | Create a directory and all parents |
| `move_file` | `workspace.edit` | write drains | Move or rename a file/directory |
| `copy_file` | `workspace.edit` | write drains | Copy a file/directory |
| `delete_path` | `workspace.delete` | write drains | Delete file or directory (distinct destructive capability) |
| `git_status` | `git.status_read` | all | Current git status |
| `git_diff` | `git.diff_read` | all | Current git diff |
| `git_log` | `git.status_read` | all | Recent commit log |
| `git_show` | `git.status_read` | all | Show a git object |
| `exec` | `process.exec_bounded` | write drains | Execute a bounded subprocess from the workspace root |
| `unsafe_exec` | `process.exec_unbounded` | write drains | Execute an unrestricted shell command in the real workspace directory (use when `exec` sandbox overhead is too high) |
| `raw_exec` | `process.exec_unbounded` | write drains | Alias for `unsafe_exec` — same handler, same permissions, unrestricted shell execution with no sandbox overhead |
| `ralph_submit_md_artifact` | `artifact.submit` | all | Validate and submit one complete markdown artifact document |
| `ralph_verify_md_artifact` | `artifact.plan_read` | all | Check a markdown artifact without persisting it; diagnostics match submission |
| `ralph_stage_md_artifact` | `artifact.submit` | all | Stage a large markdown artifact incrementally: append to (or replace) a persisted draft; returns section outline and non-gating diagnostics |
| `ralph_get_md_draft` | `artifact.plan_read` | all | Return the staged markdown draft and its current diagnostics (resume after interruption) |
| `ralph_discard_md_draft` | `artifact.submit` | all | Discard the staged markdown draft for one artifact type |
| `ralph_finalize_md_artifact` | `artifact.submit` | all | Validate the assembled draft with the submission gate and submit it canonically; on failure the draft is kept for repair |
| `ralph_edit_md_plan_step` | `artifact.submit` | all | Edit one step by stable S-id in the persisted markdown plan draft |
| `ralph_index_status` | `workspace.metadata_read` | all | Report the indexed exploration index health and freshness (lexical + Python/Markdown structure + graph) |
| `ralph_reindex` | `workspace.read` | all | Run a bounded changed/full reindex of the indexed exploration index |
| `report_progress` | `run.report_progress` | write drains, commit drains | Report progress to the pipeline |
| `declare_complete` | `artifact.submit` | all | Declare that the agent has finished |
| `coordinate` | `artifact.plan_write` | planning | Parallel worker coordination |
| `read_env` | `env.read` | write drains | Read an environment variable |
| `web_search` | `web.search` | non-commit drains (default-enabled) | Search the web via configured backends |
| `visit_url` | `web.visit` | non-commit drains (granted by default) | Fetch and extract text from a single URL |
| `download_url` | `web.visit` | non-commit drains (granted by default) | Download a URL to a workspace path; bounded by `max_bytes`; same SSRF posture as `visit_url` |
| `read_media` | `media.read` | all (default-on; opt-out via mcp.toml) | Read a media file — images, PDFs, documents, audio, video; inline or resource-reference delivery based on model capability |
| `read_image` | `media.read` | all (default-on; opt-out via mcp.toml) | Compatibility alias for `read_media` for image inputs; follows the same capability-aware delivery contract (inline image when supported, resource reference or explicit error otherwise) |
| `ralph_graph` | `workspace.read` | all | Graph-native queries against the indexed substrate (`neighbors` / `path` / `impact` / `hubs` / `tests`) with bounded `timeout_ms` and cooperative `cancel`; see [Indexed exploration](#indexed-exploration) below |

Claude exposes every tool as `mcp__ralph__<tool>` (e.g., `mcp__ralph__read_file`).
See `ralph.mcp.tools.names` for the canonical name constants.

### exec invocation notes

`exec` accepts any of these calling styles:

- `{"command": "python", "args": ["-m", "pytest"]}`
- `{"command": "python -m pytest"}`
- `{"command": ["python", "-m", "pytest"]}`
- `{"argv": ["python", "-m", "pytest"]}`

Quoted arguments inside string forms are preserved, so values containing spaces stay as a single argument. When a command **string** carries an unquoted control operator (`|`, `&&`, `||`, `;`, `>`, `<`), `exec` runs it through `sh -c` so the pipeline is interpreted normally — but the command blacklist (privilege escalation, destructive system commands, external `curl`/`wget`, network tunnels, container escapes, bulk file operations) is enforced against **every** command in the pipeline first, so a blacklisted command hiding after a separator (`echo hi; sudo rm -rf /`) is still denied. An argv **array** (`{"command": ["ls", "|", "grep", "py"]}`) is always literal argv and is never shell-interpreted. If you need file edits, git operations, or structured reads, prefer the dedicated MCP tools.

`exec` runs inside a private, resettable sandbox pool keyed by the absolute workspace path. The pool keeps the isolation contract strict while avoiding per-run overlay churn:

- each leased sandbox slot is fully reset before the subprocess starts
- the slot worktree is removed again when the exec call finishes
- writes and git mutations stay inside the leased sandbox slot, never the real workspace
- same-workspace concurrent exec calls can run in parallel by leasing different sandbox slots from the pool
- cross-workspace exec calls remain isolated because each workspace hash gets its own pool namespace
- Ralph Workflow persists a small learned target slot count per workspace and can grow or shrink the pool over time based on observed contention

This pooling behavior is an internal optimization and concurrency contract, not a public API surface. The observable `exec` semantics stay the same: bounded subprocess execution from the workspace root with strict workspace isolation.

### exec vs unsafe_exec / raw_exec

`exec` interprets shell operators (`&&`, `|`, `||`, `;`, `>`, `<`) in a command string, but keeps its command blacklist enforced against every command in the pipeline. Reach for `unsafe_exec` (or its alias `raw_exec`) only when you need a command the blacklist forbids on the bounded path — those tools run the command directly with **no** blacklist (only version control commands `git`, `hg`, `svn` are blocked) and require the separate `process.exec_unbounded` capability. Use with caution.

### read_file response shapes

`read_file` returns different response shapes depending on which parameters are supplied
and how large the file is.

**1. Plain text** — full file is UTF-8 and at or below the size limit (default 5 MB):
returned as a single text content block with no JSON envelope.

**2. Partial-read JSON envelope** — when any of `line_start`/`line_end`, `offset`/`limit`,
`head`, or `tail` is supplied. **These groups are mutually exclusive; use exactly one.**
Combining two active groups (e.g. `line_start` with a non-zero `offset`) raises `InvalidParams`.
Broker-supplied zero defaults (`offset=0`, `limit=0`, `head=0`, `tail=0`, `line_start=0`,
`line_end=0`) are treated as absent and do not count as choosing a mode.

Line-range / head / tail mode returns `total_lines` and `returned_lines`:

```json
{
  "path": "/workspace/src/example.py",
  "content": "line 10\nline 11\nline 12",
  "total_lines": 120,
  "returned_lines": 3,
  "truncated": false
}
```

Byte-window mode (`offset`/`limit`) returns `total_bytes` and `returned_bytes`:

```json
{
  "path": "/workspace/src/example.py",
  "content": "partial file content here",
  "total_bytes": 50000,
  "returned_bytes": 1000,
  "truncated": true
}
```

**3. Oversize/error JSON envelope** — when the file exceeds `max_bytes` (default
`5_000_000`) or fails UTF-8 decoding. The JSON envelope only appears in these
error/truncation cases.

Oversize truncation (`is_error: false`):

```json
{
  "path": "/workspace/logs/huge.log",
  "content": "first kilobytes of the file...",
  "truncated": true,
  "total_bytes": 12400000,
  "max_bytes": 5000000,
  "reason": "oversize"
}
```

Non-UTF-8 / binary file (`is_error: true`):

```json
{
  "status": "binary_or_invalid_utf8",
  "path": "/workspace/assets/logo.png",
  "error": "utf-8 decode failed at byte 128",
  "byte_offset": 128
}
```

Pass an explicit `max_bytes` parameter to override the 5 MB ceiling for a single call.

### Capability grant rules

Capability grants follow these rules (implemented in `ralph.mcp.session_plan`):

- **Base capabilities** (all drains): `workspace.read`, `workspace.metadata_read`, `git.status_read`, `git.diff_read`, `artifact.submit`
- **Write drains** (development, fix) additionally receive: `workspace.write_ephemeral`, `workspace.write_tracked`, `workspace.edit`, `workspace.delete`, `process.exec_bounded`, `run.report_progress`, `env.read`
- **Commit drains** (development\_commit, review\_commit, commit) are strictly read-only; they additionally receive only `run.report_progress`. `git.write` is reserved to the orchestrator and is never granted to agents.
- **`web.search`** is granted when enabled in MCP config AND the drain class is not `analysis` (development\_analysis, review\_analysis) or `commit`
- **`web.visit`** is granted to non-commit drains when enabled in MCP config; commit-class drains (development\_commit, review\_commit, commit) do not receive `web.visit`
- **`upstream.tool_use`** is granted whenever upstream MCP servers are configured, except for commit-class drains

## Artifact Submission

Agents use `ralph_submit_md_artifact` to submit one complete markdown document per
artifact type (parameters: `artifact_type`, `content`). Each type has a registered
markdown spec (`ralph/mcp/artifacts/markdown/specs/`); validation returns line-anchored
diagnostics (`line`, `section`, `rule_id`, `message`, `severity`). Any `error`-severity
diagnostic rejects the submission and nothing is persisted; `warning` diagnostics are
reported but do not block. The repair loop is: fix the markdown the diagnostics point
at (the format docs under `.agent/artifact-formats/` describe each type's expected
shape), optionally re-check with `ralph_verify_md_artifact`, then retry
`ralph_submit_md_artifact`. For staged `plan` documents,
`ralph_edit_md_plan_step` applies and persists a single step edit by stable
`S-<n>` ID.

Large documents can be authored incrementally: `ralph_stage_md_artifact` accumulates
markdown into a persisted per-type draft (reporting a section outline and non-gating
diagnostics after each call), `ralph_get_md_draft` returns the draft for resumption
after an interruption, `ralph_discard_md_draft` deletes it, and
`ralph_finalize_md_artifact` runs the full submission gate over the assembled draft —
submitting canonically on success and keeping the draft for repair on failure.

| Artifact type | Submitted by | Description |
|---------------|-------------|-------------|
| `plan` | planning agent | Structured implementation plan with steps, summary, and optional work units |
| `development_result` | developer agent | Proof-bearing implementation result with summary, changed files, proof entries, and partial-result continuation fields |
| `issues` | reviewer agent | List of issues found during review, each with severity and fix guidance |
| `fix_result` | fix agent | Summary of fixes applied and residual issues |
| `commit_message` | commit agent | Conventional commit message for the changes |
| `commit_cleanup` | commit-cleanup agent | Cleanup actions for transient or internal files before commit |
| `development_analysis_decision` | analysis agent (development) | Decision on whether to proceed, loop, or escalate after development |
| `planning_analysis_decision` | analysis agent (planning) | Decision on whether to approve, revise, or restart the plan |
| `review_analysis_decision` | analysis agent (review) | Decision on whether to pass, loop review, or escalate after review |
| `smoke_test_result` | smoke-test agent | Structured result of a smoke-test run |
| `product_spec` | planning/product-spec agent | Structured product spec artifact |

Format docs for each type live in `ralph/mcp/artifacts/format_docs/`. Agents are
directed to read the relevant format doc before retrying a failed submission.

## Upstream MCP Servers

Ralph Workflow can proxy tools from user-configured MCP servers. Register a server in
`.agent/mcp.toml`:

```toml
[[servers]]
name = "my-docs"
command = ["npx", "my-mcp-server"]

# Optional: pass environment variables
[servers.env]
MY_API_KEY = "..."
```

Once registered, every tool exposed by `my-docs` becomes available to agents under the
prefix `ralph_upstream__my-docs__<tool>`. Ralph Workflow proxies calls through and translates
responses back to the agent.

Validate your upstream server registration:

```bash
ralph --check-mcp
```

Run this from the human operator shell outside any Ralph-managed agent session.

See [Local Web Access](advanced-mcp-configuration.md#web-access-search-visit-crawl) for a worked example using Crawl4AI.

## Capability Flags

Each session drain receives a set of capability flags. Flags gate which tools are
callable. The capability strings are:

| Capability | What it gates |
|------------|--------------|
| `workspace.read` | `read_file` (full), `read_multiple_files`, `list_allowed_roots`, `list_directory`, `list_directory_recursive`, `directory_tree`, `search_files`, `grep_files` |
| `workspace.metadata_read` | `stat_path` (file metadata/stat) |
| `workspace.write_ephemeral` | Write to files not tracked by git |
| `workspace.write_tracked` | `write_file` (git-tracked files) |
| `workspace.edit` | `edit_file`, `append_file`, `create_directory`, `move_file`, `copy_file` |
| `workspace.delete` | `delete_path` (distinct destructive capability) |
| `process.exec_bounded` | `exec` (with command blacklist enforced) |
| `artifact.submit` | `ralph_submit_md_artifact`, `ralph_stage_md_artifact`, `ralph_finalize_md_artifact`, `ralph_discard_md_draft`, `ralph_edit_md_plan_step`, `declare_complete` |
| `artifact.plan_read` | `ralph_verify_md_artifact`, `ralph_get_md_draft` |
| `artifact.plan_write` | `coordinate` |
| `run.report_progress` | `report_progress` |
| `git.status_read` | `git_status`, `git_log`, `git_show` |
| `git.diff_read` | `git_diff` |
| `env.read` | `read_env` |
| `upstream.tool_use` | Upstream proxy tools (granted when upstream servers are configured) |
| `web.search` | `web_search` (default-enabled; restricted to non-commit drains) |
| `web.visit` | `visit_url` (default-enabled; non-commit drains) |
| `media.read` | `read_media` (primary, default-on; opt-out via `mcp.toml`), `read_image` (compatibility alias) |

Ralph-managed same-workspace parallel workers are dormant in the bundled default (see [Parallel Mode](advanced-pipeline-configuration.md#parallel-execution-agent-driven)). The note below describes the opt-in contract for the `ralph_fan_out` dispatch mode.

**Same-workspace parallel workers** — Parallel workers in same-workspace mode inherit the parent phase's `SessionMcpPlan` contract, which includes the resolved capability profile, model identity, and drain. This means workers expose the same multimodal capability surface as serial execution: delivery verdicts (inline image, typed block, resource reference replay, explicit unsupported) are provider-specific and consistent with the serial path, and worker-produced media artifacts are written under the worker's namespace with the phase-scoped handoff path.

See `ralph.mcp.protocol.capability_mapping` for the full capability-to-tool mapping and
`ralph-workflow/ralph/mcp/ARCHITECTURE.md` for the capability system design.

## Indexed exploration

Ralph Workflow maintains a deterministic SQLite+FTS5 indexed exploration
substrate under `.agent/ralph-explore/` for the current workspace. The
substrate is:

* disposable — deleting `.agent/ralph-explore/` forces a cold rebuild and never affects source files or workflow artifacts;
* git-ignored — the existing `.agent/` rule in `ralph/config/bootstrap.py:_DEFAULT_GITIGNORE_PATTERNS` covers it (no new entry required);
* fail-open — agents never block indefinitely on a refresh; tools return stale metadata instead of hanging.

### Lifecycle

Ralph Workflow refreshes the index deterministically:

* before every development/fix agent invocation (bounded changed-file refresh);
* after the agent invocation returns (bounded changed-file refresh);
* on every successful workspace mutation (`write_file`, `edit_file`, `append_file`, `move_file`, `copy_file`, `delete_path`), which marks the affected paths dirty;
* on demand via `ralph_reindex`.

Agents do not need to call `ralph_reindex`; the lifecycle hooks keep the index fresh enough.

### `ralph_index_status`

Reports the live index health:

```
{
  "enabled": true,
  "index_exists": true,
  "generation": 1,
  "indexed_at": 1717000000.0,
  "files_indexed": 12,
  "files_stale": 0,
  "last_job": {...},
  "capabilities": ["evidence_lookup", "fts_search"],
  "graph_backend": "sqlite",
  "dirty_paths_count": 0,
  "cold_index_required": false,
  "last_refresh_kind": "changed",
  "is_stale": false,
  "stale_paths_count": 0,
  "index_storage_bytes": 4096,
  "gitignore_coverage": {"present": true, "rule": ".agent/"},
  "managed_ignore_rule_present": true,
  "managed_ignore_rule_repair": {
    "required": false,
    "action": "none",
    "reason": "managed_ignore_rule_present"
  }
}
```

When the managed ignore rule is absent, `managed_ignore_rule_repair` carries
the next Ralph Workflow seeding instruction so callers can repair the
`.agent/ralph-explore/` coverage without guessing:

```
"managed_ignore_rule_repair": {
  "required": true,
  "action": "seed_default_gitignore",
  "reason": "managed_ignore_rule_missing",
  "target_file": "/path/to/.gitignore",
  "patterns_to_append": [".agent/"],
  "next_command": "ralph",
  "description": "Run a normal `ralph` invocation (or `auto_seed_default_gitignore`) to seed the default .gitignore so .agent/ralph-explore/ stays a disposable cache and is not committed."
}
```

The status handler never mutates the gitignore; the repair is a documented
next step. The disabled payload (no handle) carries the same repair field so
callers do not have to special-case the `enabled=False` path.

### `ralph_reindex`

Required param: `mode` in `changed | full`. Optional: `timeout_ms` (1-60000,
default 5000; out-of-range or malformed values are rejected), `path_scope`
(list of relative paths). Returns `job_status`, `generation`, `changed_files`,
`failed_files`, `parse_count`, `dirty_paths_count`, `elapsed_seconds`,
`error_summary`. The handler enforces a maximum permissible `timeout_ms` so
callers cannot extend the budget arbitrarily. `mode='full'` rebuilds into a
temp generation and atomically swaps metadata only after success.

### Indexed arguments on existing tools

The shipped indexed exploration adds optional indexed arguments to existing read/search tools; the legacy behavior is preserved when the argument is absent or set to `use_index="never"`. `span_id`, `symbol`, `contains_symbol`, `return_evidence_ids`, `ranked`, `role`, and `changed_only` are backed by the live spans/symbols/edges tables and never return `disabled:phase2` for shipped capabilities:

* `grep_files(use_index, rank_by, return_evidence_ids, max_snippet_lines, dedupe_by_symbol, include_graph_context)`. Eligibility: literal, whole-word literal, simple token, phrase. Non-eligible (regex, multiline, lookaround, backreferences, byte-oriented) falls back to live grep in `auto` and fails closed in `always`. `rank_by` accepts `match`, `symbol`, `graph`, `changed`, or `hybrid`. The symbol and graph components add their bonus only when the explore index has the relevant rows; when context is absent the reason line records `+0 component:no_indexed_data` so callers see why a component did not contribute.
* `search_files(ranked, role, contains_symbol, changed_only, return_evidence_ids)`. `contains_symbol` awards the indexed `SEARCH_SYMBOL_MENTION` score component when the index has symbol rows; otherwise the rank degrades to deterministic path/role scoring with an explicit `+0 component:no_indexed_data` reason.
* `read_file(evidence_id, span_id, symbol, context_lines, expected_content_hash, return_metadata)`. `span_id` and `symbol` resolve via the explore index when present; missing span/symbol lookups return `unknown_evidence` (or `ambiguous_symbol` when multiple candidates match). `expected_content_hash` fails closed before any mutation.
* `read_multiple_files(items, per_item_max_bytes, return_metadata, fail_fast)`. Items may mix `{"path": ...}`, `{"path": ..., "line_start": ..., "line_end": ...}`, `{"evidence_id": ...}`, `{"span_id": ...}`, or `{"symbol": ...}`. Per-item metadata reports truncation, freshness, and fallback reason.

### Indexed selection exclusivity

`read_file` accepts exactly one of `path`, `evidence_id`, `span_id`, or `symbol`. Passing two or more selectors, or none, fails closed with a structured invalid-parameter error so the legacy wire shape is preserved while the indexed path is strictly exclusive. `read_multiple_files` accepts either a legacy `paths` list or an `items` list of mixed selectors; passing both, or neither, fails closed.

### `ralph_graph`

`ralph_graph` is registered alongside the read/search tools and answers graph-native questions. Shared inputs: `query_type` in `neighbors | path | impact | hubs | tests`, `target`, `relations`, `limit` (default 25, max 100), `freshness` in `required | prefer_fresh | allow_stale`, `timeout_ms` (1-30000, default 5000), `cancel` (bool, default false). Per-query inputs: `direction`/`depth` (neighbors); `target_b`/`max_paths`/`depth` (path); `change_kind` in `rename | signature | behavior | delete | unknown` (impact); `scope_path`/`relation`/`role` (hubs and tests). Every response includes `nodes`, `edges`, `paths`, `impacted_files`, `suggested_tests`, `confidence`, `provenance`, `evidence_ids`, `missing_data`, `index_generation`, `is_stale`, `truncated`, `cancelled`, `deadline_exceeded`. Graph output is evidence-backed and labels inferred or unknown data rather than claiming runtime certainty.

**Bounded timeout and cancellation:** `timeout_ms` is a bounded per-call budget (1-30000). When the deadline elapses, the dispatcher returns a bounded, truthful incomplete result with `deadline_exceeded=true` and `missing_data=("deadline_exceeded",)`. `cancel=true` returns the same bounded contract with `cancelled=true` and `missing_data=("cancelled",)`. No mutable work is exposed to readers on either path.

### `list_directory` and `directory_tree` indexed views

`view` accepts `raw | compact | ranked | outline`. The raw view preserves the legacy plain-text/tree shape unless an explicit indexed selector is requested (for example `view=compact`, `include_counts=true`, `include_symbols=true`, `changed_only=true`, or `use_index=always`). `use_index` accepts `auto | always | never`; `never` is an unconditional bypass of the explore index. `changed_only` filters to entries with a dirty (mutated) descendant and respects the same dirty-path source as the mutation handlers. `directory_tree` decorates every node with a `path` field and decorates children before ranking, so `view=ranked` orders by indexed symbol counts.

### `edit_file` indexed safety arguments

`edit_file` accepts `expected_content_hash`, `target` (`evidence_id` / `span_id` / `symbol`), `match_strategy` in `exact | within_target | all_in_target`, `reindex` in `auto | skip | changed_blocking`, `impact_preview` (requires `dry_run=true`), and `return_evidence_updates`. Hash mismatches and stale evidence fail closed before any mutation. `impact_preview` runs a conservative `ralph_graph` impact query when the index has the target; otherwise it returns `impact_preview_unavailable` plus `impact_preview_unavailable_reason`.

### Mutation freshness metadata

Every successful `write_file`, `edit_file`, `append_file`, `move_file`, `copy_file`, and `delete_path` call returns a freshness block: `index_used`, `index_generation`, `is_stale`, `dirty_paths_count`, `stale_paths_count`, `reindex_in_progress`, and `changed_paths`. The block is omitted only when the explore index is disabled; the prompt never requires an agent to call `ralph_reindex` to keep the index current.

### Indexed responses

Every indexed response includes `index_used`, `index_generation`, `is_stale`, `stale_paths_count`, `dirty_paths_count`, `fallback_reason`. When `index_used=false`, the response came from live behavior; the caller can decide whether to retry.

### Phase 1 / Phase 2 / Phase 3 / Phase 4 scope

* Phase 1 is the lexical layer: FTS5 chunking + content hash + evidence handles. Storage is bounded: job history caps at 100/14 days, evidence tombstones at 10k/30 days, and the index lives under `.agent/ralph-explore/`. The bootstrap seeder appends both the parent `.agent/` rule and the explicit `.agent/ralph-explore/` child rule so the disposable cache coverage is reported transparently in `.gitignore`.
* Phase 2 ships Python AST and Markdown structure extraction in `ralph.mcp.explore.structure`. Spans, symbols, and edges live in the `spans`, `symbols`, and `edges` tables with `provenance` (`extracted` / `inferred` / `ambiguous`), `confidence`, and `extractor_version`. The relation set covers `contains`, `defines`, `imports`, `calls_syntax`, `references_text`, `inherits_syntax`, `tests`, and `mentions`. Malformed Python raises a typed `PythonExtractionError` that the reindex pipeline catches in its preflight so lexical/structure rows for the path remain queryable while the path is reported in `failed_files` and retried on the next pass. `ralph_graph` is the graph-native query surface (`neighbors`, `path`, `impact`, `hubs`, `tests`) with bounded per-call deadlines and cooperative cancellation.
* Phase 3 wires `edit_file` safety arguments (`expected_content_hash`, `target`, `match_strategy`, `reindex`, `impact_preview`, `return_evidence_updates`) and the conservative impact preview through `ralph_graph`.
* Phase 4 ships the compact/summary output modes for `git_status` (`format=compact`), `git_diff` (`format=summary`), and `exec` (`format=summary`). Phase 4 also ships `format=summary` for `git_log` and `git_show`, `format=summary` for `web_search` and `download_url`, `format=metadata` for `visit_url`, and `format=metadata` for `read_image` and `read_media`. The audit register records these as `add_argument` outcomes with a deterministic Phase 0 rationale; the per-tool `AuditCounters` are seeded in `ralph.mcp.explore._audit_seed_*` and can be overlaid by measured `run_benchmark` results via `refresh_audit_register(measurements)` (see `tests/test_explore_audit_register.py` for the deterministic overlay contract); `unsafe_exec` and its `raw_exec` alias are kept unchanged (`keep`) because the summary mode is intentionally only on the bounded exec path. The markdown artifact tools and the coordination tools are audited as `keep` because their existing structured behavior (bounded validation-diagnostic envelopes, bounded coordination payloads with structured marker suffixes) already matches the Phase-4 acceptance contract. No audited tool remains in an `audit found inefficient but no decision` state. `exec` summary mode returns `stdout_resource_id` and `stderr_resource_id` handles of the form `ralph://exec/<spill-name>`; production sessions attach an `ExecResourceResolver` in `ralph.mcp.tools._exec_resource_uri` so those handles are replayable through `resources/read` (the resource template `ralph://exec/{spill_name}` is registered alongside `ralph://media/{artifact_id}`). Sessions without the resolver return a structured "resolver not attached" error so legacy clients get a consistent failure mode while the raw output remains available.
* Phases 0-4 are all shipped; the only remaining deferred register entry is `phase_5` (NetworkX / Kuzu / hybrid ranking / Tree-sitter) tracked in `ralph.mcp.explore.deferred_phases` and gated on measured SQLite bottleneck evidence.

### Compact format args (Phase 4)

The following format args are added by Phase 4. Each is opt-in: the default value preserves the legacy output byte-for-byte so existing callers are unaffected. Invalid values return a structured `is_error=true` result naming the closed enum.

| Tool | Format arg | Default | Summary/Metadata shape |
|------|------------|---------|------------------------|
| `git_log` | `format='raw'\|'summary'` | `'raw'` | `{"format": "summary", "count": int, "commits": [{"short_sha", "sha", "subject"}], "bytes_in", "bytes_out"}` |
| `git_show` | `format='raw'\|'summary'` | `'raw'` | `{"format": "summary", "ref", "kind" (commit\|tag), "sha", "short_sha", "author_name", "author_email", "author_date", "subject", "parents", "bytes_in", "bytes_out", "truncated": false}` (no patch body) |
| `web_search` | `format='raw'\|'summary'` | `'raw'` | `{"format": "summary", "query_length", "result_count", "results": [{title, url, snippet (<=240 chars), snippet_budget_bytes}], "backend_chain_used", "bytes_in", "bytes_out"}` |
| `visit_url` | `format='raw'\|'metadata'` | `'raw'` | `{"format": "metadata", "status", "title", "effective_url", "content_type", "byte_count", "head_preview" (<=480 chars), "bytes_in", "bytes_out", "truncated", optional links (<=10)}` (full text body dropped) |
| `download_url` | `format='raw'\|'summary'` | `'raw'` | `{"format": "summary", ..., "sha256" (16 hex chars), "head_preview" (<=240 bytes), "bytes_in", "truncated"}` (downloaded body NOT echoed inline) |
| `read_image` | `format='inline'\|'metadata'` | `'inline'` | `{"format": "metadata", "media_kind": "image", "mime_type", "size_bytes", "sha256", "width", "height" (PNG only), "resource_handle" (always null for read_image), "inline_only": true, ...}` |
| `read_media` | `format='inline'\|'metadata'` | `'inline'` | `{"format": "metadata", "media_kind", "mime_type", "size_bytes", "sha256", "resource_handle" (`ralph://media/<artifact-id>` when registered, else null), "inline_only", "title", "path", ...}` |

Example payloads:

```json
// git_log format=summary
{"format": "summary", "count": 2, "commits": [{"short_sha": "abc1234", "sha": "abc1234", "subject": "first commit"}], "bytes_in": 32, "bytes_out": 128}

// visit_url format=metadata (no inline text body)
{"format": "metadata", "status": "ok", "title": "Hello", "effective_url": "https://example.com/", "content_type": "text/html; charset=utf-8", "byte_count": 1024, "head_preview": "...", "bytes_in": 4096, "bytes_out": 256, "truncated": true}

// read_media format=metadata (resource_handle preserved for resource-reference deliveries)
{"format": "metadata", "media_kind": "pdf", "mime_type": "application/pdf", "size_bytes": 1024, "sha256": "abcdef0123456789", "resource_handle": "ralph://media/12345678", "inline_only": false, "title": "report.pdf", "path": "report.pdf", "bytes_in": 1024, "bytes_out": 256, "truncated": false}
```

The optional `ralph_explore` wrapper remains deferred and is tracked in `ralph.mcp.explore.deferred_phases`. Optional NetworkX offline metrics, Kuzu adapters, and additional Tree-sitter parsers are also deferred until measured evidence justifies them.

## Related pages

- [Concepts](concepts.md) — MCP, drains, capabilities, and artifact types
- [Local Web Access](advanced-mcp-configuration.md#web-access-search-visit-crawl) — `visit_url` SSRF posture and upstream crawlers
