# MCP Tool Restriction Guarantees

Ralph Workflow enforces MCP-only tooling by disabling native agent tools at the CLI and config layer. This document describes how that enforcement works for each supported CLI, known limitations, and how to verify it is active.

## 1. Overview

Ralph Workflow is designed as an opinionated AI agent orchestration framework rooted in the Ralph Workflow loop, where every tool call should produce an auditable action with a traceable identity. Native agent tools like `Read`, `Write`, `Edit`, and `Bash` bypass the MCP bridge and therefore break Ralph Workflow's audit trail, capability mapping, and policy enforcement.

Ralph Workflow's prompts claim "Native agent tools are DISABLED". This document describes how the CLI and config layer enforces that claim at invocation time for each supported backend, and where config preservation is separate from strict policy enforcement:

- **Claude Code** receives `--tools ""` plus a strict-MCP-config that contains only the Ralph Workflow MCP server.
- **OpenCode** receives a config payload that explicitly sets each native tool to `false`.
- **Codex** receives a TOML config that preserves existing sections and disables several built-in features, but core editing primitives cannot be fully removed.

### Strict Ralph Workflow Authority Mode

In strict Ralph Workflow authority mode, provider CLIs receive only the Ralph Workflow MCP endpoint. User-configured upstream MCP servers are loaded by Ralph Workflow itself and re-exposed as Ralph Workflow-owned proxied tool aliases under the `ralph_upstream__<server_name>__<tool_name>` naming scheme. Provider-side MCP permissions must not be relied on for these proxied tools. Ralph Workflow enforces capability policy before forwarding any proxied tool call to its upstream backend. This contract applies consistently to Claude, OpenCode, and Codex integrations.

## 2. Per-CLI Guarantees

### Claude Code — Full Enforcement

Claude Code supports CLI flags that together remove all native tools from a session:

- `--tools ""` — An empty allowlist disables every native tool. The empty string is not a wildcard; it means "allow nothing".
- `--strict-mcp-config` — Ignores Claude's default global and workspace MCP config discovery. Ralph Workflow reads supported user config files (`~/.claude.json`, workspace `.mcp.json`, workspace `.claude.json`) to extract upstream MCP server definitions, but does **not** pass those definitions to Claude as MCP servers. Instead, Ralph Workflow loads those upstream servers itself and re-exposes their tools as Ralph Workflow-owned proxied aliases. The generated `--mcp-config` contains only the Ralph Workflow MCP server entry.

Ralph Workflow passes `--allowedTools` for Claude using the exact live Ralph Workflow MCP tool names reported by the runtime endpoint. This keeps built-in tools disabled via `--tools ""` while pre-approving only Ralph Workflow-owned MCP tools for the current session. Ralph Workflow still remains the real policy boundary: provider approval only removes Claude-side prompts, while `ToolBridge` metadata and session capabilities decide whether the forwarded call is actually allowed.

Reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference

### OpenCode — Full Enforcement

OpenCode reads configuration from a JSON object passed via the `OPENCODE_CONFIG_CONTENT` environment variable. Ralph Workflow builds this object in `_merge_opencode_config_content()` and disables all 16 native tools by setting each to `false`:

```
bash, codesearch, edit, glob, grep, list, lsp, patch, question, read, skill, task, todowrite, webfetch, websearch, write
```

The key mechanism is dict-spread merge:

```python
disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
config_obj["tools"] = {**existing_tools, **disable_overrides}
```

Because Ralph Workflow's disable entries come after the spread of existing user config, Ralph Workflow's `false` values win over any user-provided `tools.bash: true`. The MCP policy overrides user enables while still preserving unrelated `permission` and non-native `tools` entries.

In strict Ralph Workflow authority mode, the provider-visible `mcp` field in this config contains only the Ralph Workflow MCP server entry. User-configured upstream MCP servers are extracted by Ralph Workflow and re-exposed as Ralph Workflow-owned proxied tool aliases. Provider-side MCP permissions are not the authority for those proxied tools.

Reference: https://opencode.ai/docs

### Codex — Best-Effort Only

Codex is configured via a `config.toml` file. Ralph Workflow prepares this file in `_prepare_codex_home()` by preserving the user's existing `config.toml`, replacing any stale `[mcp_servers.ralph]` block with the live run-scoped endpoint, and setting the following in the `[features]` block:

```
features.shell_tool = false
features.multi_agent = false
features.undo = false
features.apps = false
web_search = "disabled"
```

These settings reduce the attack surface, but **`apply_patch` and core file-editing primitives cannot be disabled**. Codex has no comprehensive MCP-only mode and no `--tools` CLI flag equivalent. When an MCP endpoint is wired to Codex, Ralph Workflow logs a WARNING at every invocation:

```
Codex MCP wiring is best-effort; disabling built-in features for <endpoint>
```

In strict Ralph Workflow authority mode, the provider-visible `[mcp_servers]` section contains only the run-scoped `ralph` entry for the Ralph Workflow MCP server. User upstream MCP server definitions are extracted and passed to Ralph Workflow separately; they are not included in the provider-visible config. Ralph Workflow re-exposes upstream tools as Ralph Workflow-owned proxied aliases.

Do not rely on Codex for environments that require strict tool isolation. Ralph Workflow's best-effort for Codex is explicitly logged as a warning at runtime.

Reference: https://platform.openai.com/docs/codex

## 3. Known Bugs and Limitations

### Claude Code

- **Bug #25589**: `--disallowedTools` ignores MCP tools when combined with `--mcp-config`. Ralph Workflow avoids this by using `--tools ""` instead of a disallowed-list approach.
- **Bug #13077**: `--allowedTools` wildcards do not match MCP tools. Ralph Workflow avoids wildcard-based Claude approvals and instead derives an exact per-session Ralph Workflow MCP allowlist from the live runtime endpoint.
- **Bug #32079**: `--tools ""` combined with `--mcp-config` and a system prompt larger than 18 KB causes Claude Code to exit silently. Ralph Workflow's system prompt is under 1 KB. If the prompt ever grows beyond 18 KB, this document must be updated and a mitigation applied.

### OpenCode

- The `OPENCODE_CONFIG_CONTENT` mechanism is verified empirically. OpenCode does not document an official API contract for config injection at startup. If a future OpenCode release changes how this is processed, enforcement may regress without warning.

### Codex

- No `--tools` CLI flag or equivalent exists for Codex. There is no native-tool-free mode.
- `apply_patch` and core editing primitives remain active regardless of `[features]` settings.
- The `web_search = "disabled"` string literal is required because Codex TOML interprets bare `disabled` as an identifier, not a string.

## 4. How Ralph Workflow Verifies Enforcement

Ralph Workflow's test suite covers enforcement through agent invocation tests:

- **`tests/test_agents_invoke.py`** verifies Claude uses `--tools ""`, derives `--allowedTools` from Ralph Workflow-only MCP tool names, the provider-visible `--mcp-config` contains only the Ralph Workflow MCP server entry, and upstream server definitions are extracted and passed to Ralph Workflow runtime separately for proxied re-exposure.
- The same file verifies OpenCode JSON config generation disables all 16 native tools while preserving unrelated non-tool config fields (for example, `permission`). The provider-visible `mcp` field contains only the Ralph Workflow MCP server entry; user upstream MCP servers are extracted and passed to Ralph Workflow separately for proxy re-exposure.
- For Codex, the same file checks that the generated `config.toml` preserves unrelated sections, does not include user upstream `mcp_servers` in the provider-visible `[mcp_servers]` section (they are extracted and passed to Ralph Workflow separately for proxy re-exposure), and rewrites any stale `[mcp_servers.ralph]` block to the live endpoint.

Transport selection and alias routing are verified in **`tests/test_agent_registry.py`**, which checks that `ccs` aliases resolve to the correct CLI and that each CLI receives the appropriate transport configuration.

Run these tests with:

```bash
cd ralph-workflow
pytest tests/test_agents_invoke.py -q
```

## 5. Follow-Up

### MCP Reachability Preflight

With MCP-only enforcement active, agents that encounter an unreachable MCP server have no native fallback. Claude Code, OpenCode, and Codex will all produce output when their only available tools are unavailable, but that output will not be useful and may be silently wrong.

A preflight probe that verifies MCP server reachability before launching an agent is planned. See `.agent/PLAN.md` for the full roadmap. Until that probe is implemented, an unreachable MCP server will produce a confusing failure mode that is difficult to diagnose from logs alone.

### Prompt Size Monitoring

The Claude Code bug #32079 limitation (18 KB system prompt threshold) should be monitored. If Ralph Workflow's system prompt grows significantly, a check should be added to `invoke.py` that fails fast with a clear error rather than silently exiting.
