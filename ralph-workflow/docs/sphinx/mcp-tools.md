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
| `read_file` | `workspace.read` | all | Read a UTF-8 file from the workspace |
| `write_file` | `workspace.write_tracked` | write drains | Write or overwrite a tracked file |
| `list_directory` | `workspace.read` | all | List entries in a directory |
| `list_directory_recursive` | `workspace.read` | all | Recursive directory listing |
| `directory_tree` | `workspace.read` | all | JSON directory tree |
| `search_files` | `workspace.read` | all | Glob-pattern file search |
| `git_status` | `git.status_read` | all | Current git status |
| `git_diff` | `git.diff_read` | all | Current git diff |
| `git_log` | `git.status_read` | all | Recent commit log |
| `git_show` | `git.status_read` | all | Show a git object |
| `exec` | `process.exec_bounded` | write drains | Execute a bounded shell command |
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

### Capability grant rules

Capability grants follow these rules (implemented in `ralph.mcp.session_plan`):

- **Base capabilities** (all drains): `workspace.read`, `git.status_read`, `git.diff_read`,
  `artifact.submit`
- **Write drains** (development, fix) additionally receive: `workspace.write_ephemeral`,
  `workspace.write_tracked`, `process.exec_bounded`, `run.report_progress`, `env.read`
- **Commit drains** (development\_commit, review\_commit, commit) additionally receive:
  `workspace.write_ephemeral`, `git.write`, `run.report_progress`
- **`web.search`** is granted when enabled in MCP config AND the drain class is not
  `analysis` (development\_analysis, review\_analysis) or `commit`
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
| `workspace.read` | `read_file`, `list_directory`, `list_directory_recursive`, `directory_tree`, `search_files` |
| `workspace.write_ephemeral` | Write to files not tracked by git |
| `workspace.write_tracked` | `write_file` (git-tracked files) |
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
