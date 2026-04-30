# MCP Tools Reference

Ralph Workflow runs a private MCP (Model Context Protocol) server for each agent
invocation. Agents connect to it automatically; no manual setup is needed. The server
exposes workspace access, artifact submission, coordination, and web tools — all gated
by the capability flags in effect for the current session drain.

## Native Tools

The following tools are exposed directly by Ralph Workflow's MCP server. The
capability gate column lists the capability that must be present in the session for
the tool to be callable.

Drain class groupings used in this table:

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
| `ralph_submit_artifact` | `artifact.submit` | all | Submit a structured artifact |
| `ralph_submit_plan_section` | `artifact.submit` | planning | Submit one section of the plan draft |
| `ralph_finalize_plan` | `artifact.submit` | planning | Finalize and validate the plan draft |
| `ralph_get_plan_draft` | `artifact.submit` | planning | Retrieve the current plan draft |
| `ralph_discard_plan_draft` | `artifact.submit` | planning | Discard the current plan draft |
| `report_progress` | `run.report_progress` | write drains, commit drains | Report progress to the pipeline |
| `declare_complete` | `artifact.submit` | all | Declare that the agent has finished |
| `coordinate` | `artifact.submit` | all | Parallel worker coordination |
| `read_env` | `env.read` | write drains | Read an environment variable |
| `web_search` | `web.search` | non-analysis/commit (config opt-in) | Search the web via configured backends |
| `visit_url` | `web.visit` | all (config opt-in) | Fetch and extract text from a single URL |
| `read_image` | `media.read` | opt-in via mcp.toml | Read an image file (multimodal opt-in) |

Claude exposes every tool as `mcp__ralph__<tool>` (e.g., `mcp__ralph__read_file`).
See `ralph.mcp.tools.names` for the canonical name constants.

### exec invocation notes

`exec` accepts any of these calling styles:

- `{"command": "python", "args": ["-m", "pytest"]}`
- `{"command": "python -m pytest"}`
- `{"command": ["python", "-m", "pytest"]}`
- `{"argv": ["python", "-m", "pytest"]}`

Quoted arguments inside string forms are preserved, so values containing spaces stay as a
single argument. Ralph Workflow still does **not** emulate a shell: shell control operators
such as `|`, `&&`, `;`, `>`, and `<` are rejected instead of being interpreted, and the
error message explicitly says that `exec` runs a subprocess rather than a shell. If you need
file edits, git operations, or structured reads, prefer the dedicated MCP tools.

### read_file response shapes

`read_file` returns different response shapes depending on which parameters are supplied
and how large the file is.

**1. Plain text** — full file is UTF-8 and at or below the size limit (default 5 MB):
returned as a single text content block with no JSON envelope.

**2. Partial-read JSON envelope** — when any of `line_start`/`line_end`, `offset`/`limit`,
`head`, or `tail` is supplied:

```json
{
  "path": "/workspace/src/example.py",
  "content": "line 10\nline 11\nline 12",
  "total_lines": 120,
  "returned_lines": 3,
  "truncated": false
}
```

The partial-read parameter groups are mutually exclusive; combining any two
(`line_start`/`line_end` with `offset`/`limit`, etc.) raises `InvalidParams`.

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
- **`web.visit`** is granted to all drains when enabled in MCP config
- **`upstream.tool_use`** is granted whenever upstream MCP servers are configured

## Artifact Submission

Agents use `ralph_submit_artifact` to submit structured JSON payloads. Each type has a
validated schema; an invalid payload is rejected and the error response points the agent
to the format doc at `.agent/artifact-formats/<type>.md`.

| Artifact type | Submitted by | Description |
|---------------|-------------|-------------|
| `plan` | planning agent | Structured implementation plan with steps, summary, and optional work units |
| `development_result` | developer agent | Summary of what was implemented and a self-assessment |
| `issues` | reviewer agent | List of issues found during review, each with severity and fix guidance |
| `fix_result` | fix agent | Summary of fixes applied and residual issues |
| `commit_message` | commit agent | Conventional commit message for the changes |
| `development_analysis_decision` | analysis agent (development) | Decision on whether to proceed, loop, or escalate after development |
| `review_analysis_decision` | analysis agent (review) | Decision on whether to pass, loop review, or escalate after review |

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

See [Local Web Access](local-web-access.md) for a worked example using Crawl4AI.

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
| `artifact.submit` | `ralph_submit_artifact`, `declare_complete`, `coordinate`, plan draft tools |
| `run.report_progress` | `report_progress` |
| `git.status_read` | `git_status`, `git_log`, `git_show` |
| `git.diff_read` | `git_diff` |
| `env.read` | `read_env` |
| `upstream.tool_use` | Upstream proxy tools (granted when upstream servers are configured) |
| `web.search` | `web_search` (config opt-in; restricted to non-analysis/commit drains) |
| `web.visit` | `visit_url` (config opt-in; all drains) |
| `media.read` | `read_image` (opt-in via `mcp.toml`) |

See `ralph.mcp.protocol.capability_mapping` for the full capability-to-tool mapping and
`ralph-workflow/ralph/mcp/ARCHITECTURE.md` for the capability system design.

## Related pages

- [Concepts](concepts.md) — MCP, drains, capabilities, and artifact types
- [Local Web Access](local-web-access.md) — `visit_url` SSRF posture and upstream crawlers
