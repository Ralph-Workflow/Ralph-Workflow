# MCP Tool Restriction Guarantees

Ralph Workflow enforces MCP-only **file and shell** tooling by disabling the native filesystem/exec tools at the CLI and config layer, while keeping each agent's native **orchestration** tools — sub-agents/tasks, skills, todo tracking, and web fetch/search — enabled. This document describes how that enforcement works for each supported CLI, known limitations, and how to verify it is active.

## 1. Overview

Ralph Workflow is designed as an opinionated AI agent orchestration framework rooted in the Ralph Workflow loop, where every tool call should produce an auditable action with a traceable identity. Native file and shell tools like `Read`, `Write`, `Edit`, and `Bash` bypass the MCP bridge and therefore break Ralph Workflow's audit trail, capability mapping, and policy enforcement.

Native orchestration tools do not write to the workspace directly, so they stay enabled: sub-agent/task dispatch (required for parallel plan execution), skills, todo tracking, and web fetch/search. Sub-agents spawned inside a Ralph Workflow-wired session inherit the same tool restriction and the same Ralph Workflow MCP surface, so their file and shell operations remain brokered.

Ralph Workflow's prompts claim "Native file and shell tools are DISABLED". This document describes how the CLI and config layer enforces that claim at invocation time for each supported backend, and where config preservation is separate from strict policy enforcement:

- **Claude Code** receives `--tools "Agent,Task,Skill,TodoWrite,WebFetch,WebSearch"` (the orchestration keep-list; every other built-in is removed) plus a strict-MCP-config that contains only the Ralph Workflow MCP server.
- **OpenCode** receives a config payload that explicitly sets each native filesystem/exec tool to `false` and auto-allows the orchestration keep-list.
- **Codex** receives a TOML config that preserves existing sections, disables filesystem/exec-adjacent features, and explicitly enables `multi_agent`; core editing primitives cannot be fully removed.
- **Google Anti Gravity** uses the Ralph Workflow-owned MCP proxy contract and reads existing user config files for upstream discovery, but does not have a documented environment-variable home override.

### Strict Ralph Workflow Authority Mode

In strict Ralph Workflow authority mode, provider CLIs receive only the Ralph Workflow MCP endpoint. User-configured upstream MCP servers are loaded by Ralph Workflow itself and re-exposed as Ralph Workflow-owned proxied tool aliases under the `ralph_upstream__<server_name>__<tool_name>` naming scheme. Provider-side MCP permissions must not be relied on for these proxied tools. Ralph Workflow enforces capability policy before forwarding any proxied tool call to its upstream backend. This contract applies consistently to Claude, OpenCode, Codex, and Google Anti Gravity integrations.

## 2. Per-CLI Guarantees

### Claude Code - Full Enforcement

Claude Code supports CLI flags that together restrict the native toolset of a session:

- `--tools "Agent,Task,Skill,TodoWrite,WebFetch,WebSearch"` - Restricts the built-in toolset to the orchestration keep-list (`CLAUDE_NATIVE_TOOLS_TO_KEEP` in `ralph/mcp/tools/names.py`); every filesystem/exec built-in (`Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, …) is removed. MCP tools are unaffected by `--tools`. The sub-agent dispatcher was renamed `Task` → `Agent` in claude v2.1.63; unknown names in `--tools` are silently ignored, so listing both keeps every CLI version covered. Sub-agents inherit the parent session's tool restriction and MCP servers unless their own definition overrides them.
- `--strict-mcp-config` - Ignores Claude's default global and workspace MCP config discovery. Ralph Workflow reads supported user config files (`~/.claude.json`, workspace `.mcp.json`, workspace `.claude.json`) to extract upstream MCP server definitions, but does **not** pass those definitions to Claude as MCP servers. Instead, Ralph Workflow loads those upstream servers itself and re-exposes their tools as Ralph Workflow-owned proxied aliases. The generated `--mcp-config` contains only the Ralph Workflow MCP server entry.

Ralph Workflow passes `--allowedTools` for Claude using the exact live Ralph Workflow MCP tool names reported by the runtime endpoint, plus the orchestration keep-list. `--allowedTools` only grants permissions — it cannot re-enable a tool that `--tools` removed — so the keep-list must appear in `--tools` to stay available and in `--allowedTools` to run without approval prompts. Ralph Workflow still remains the real policy boundary: provider approval only removes Claude-side prompts, while `ToolBridge` metadata and session capabilities decide whether the forwarded call is actually allowed.

Reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference

### OpenCode - Full Enforcement

OpenCode reads configuration from a JSON object passed via the `OPENCODE_CONFIG_CONTENT` environment variable. Ralph Workflow builds this object in `_merge_opencode_config_content()` and disables the 11 native filesystem/exec tools by setting each to `false`:

```
bash, codesearch, edit, glob, grep, list, lsp, patch, question, read, write
```

(`question` is disabled because it prompts the user and wedges headless runs.)

The orchestration keep-list stays enabled and is auto-allowed in the generated `permission` section so it cannot wedge a headless run on an approval prompt (`OPENCODE_NATIVE_TOOLS_TO_KEEP` in `ralph/mcp/tools/names.py`):

```
skill, task, todowrite, webfetch, websearch
```

The key mechanism is dict-spread merge:

```python
disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
config_obj["tools"] = {**existing_tools, **disable_overrides}
```

Because Ralph Workflow's disable entries come after the spread of existing user config, Ralph Workflow's `false` values win over any user-provided `tools.bash: true`. The MCP policy overrides user enables while still preserving unrelated `permission` and non-native `tools` entries.

In strict Ralph Workflow authority mode, the provider-visible `mcp` field in this config contains only the Ralph Workflow MCP server entry. User-configured upstream MCP servers are extracted by Ralph Workflow and re-exposed as Ralph Workflow-owned proxied tool aliases. Provider-side MCP permissions are not the authority for those proxied tools.

Reference: https://opencode.ai/docs

### Codex - Best-Effort Only

Codex is configured via a `config.toml` file. Ralph Workflow prepares this file in `_prepare_codex_home()` by preserving the user's existing `config.toml`, replacing any stale `[mcp_servers.ralph]` block with the live run-scoped endpoint, and setting the following in the `[features]` block (`CODEX_NATIVE_FEATURE_OVERRIDES` in `ralph/mcp/tools/names.py`):

```
features.shell_tool = false
features.multi_agent = true
features.undo = false
features.apps = false
```

`multi_agent` is explicitly **enabled** so Codex keeps its native sub-agent dispatch. `web_search` is no longer force-disabled; it is left at Codex's native default. These settings reduce the attack surface, but **`apply_patch` and core file-editing primitives cannot be disabled**. Codex has no comprehensive MCP-only mode and no `--tools` CLI flag equivalent. When an MCP endpoint is wired to Codex, Ralph Workflow logs a WARNING at every invocation:

```
Codex MCP tool restriction is best-effort: apply_patch and core editing primitives cannot be disabled.
```

In strict Ralph Workflow authority mode, the provider-visible `[mcp_servers]` section contains only the run-scoped `ralph` entry for the Ralph Workflow MCP server. User upstream MCP server definitions are extracted and passed to Ralph Workflow separately; they are not included in the provider-visible config. Ralph Workflow re-exposes upstream tools as Ralph Workflow-owned proxied aliases.

Do not rely on Codex for environments that require strict tool isolation. Ralph Workflow's best-effort for Codex is explicitly logged as a warning at runtime.

Reference: https://platform.openai.com/docs/codex

### Google Anti Gravity - Full Enforcement (Global-Config-Injection-Based)

Google Anti Gravity (AGY) is a first-class supported agent path under the same MCP enforcement contract as Claude Code and OpenCode. Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into AGY's global `~/.gemini/antigravity-cli/mcp_config.json` before AGY launches using the `agy_workspace_mcp_endpoint` context manager, and restores the file after the run. Measured behaviour shows AGY's headless `--print` mode only initialises its MCP client when this global config file exists; the workspace-level `.agents/mcp_config.json` is not sufficient. The provider-visible config written by this context manager contains only the Ralph Workflow MCP server entry, matching Ralph Workflow's strict-authority-mode contract. No manual pre-configuration of the Ralph Workflow endpoint is required. Ralph Workflow still discovers user-configured AGY upstream servers from `~/.gemini/antigravity-cli/mcp_config.json` and workspace `.agents/mcp_config.json` for the upstream proxy.

AGY participates fully in Ralph Workflow's upstream proxy model, capability-gated MCP model, and completion contract. This is a setup difference, not a capability limitation.

## 3. Known Bugs and Limitations

### Claude Code

- **Bug #25589**: `--disallowedTools` ignores MCP tools when combined with `--mcp-config`. Ralph Workflow avoids this by using a `--tools` keep-list instead of a disallowed-list approach.
- **Bug #13077**: `--allowedTools` wildcards do not match MCP tools. Ralph Workflow avoids wildcard-based Claude approvals and instead derives an exact per-session Ralph Workflow MCP allowlist from the live runtime endpoint.
- **Bug #32079**: `--tools ""` combined with `--mcp-config` and a system prompt larger than 18 KB causes Claude Code to exit silently. Ralph Workflow now passes a non-empty `--tools` keep-list, which sidesteps the empty-string variant of this bug; the prompt-size caveat is retained here in case the restriction is ever tightened back to `--tools ""`. Ralph Workflow's system prompt is under 1 KB.

### OpenCode

- The `OPENCODE_CONFIG_CONTENT` mechanism is verified empirically. OpenCode does not document an official API contract for config injection at startup. If a future OpenCode release changes how this is processed, enforcement may regress without warning.

### Codex

- No `--tools` CLI flag or equivalent exists for Codex. There is no native-tool-free mode.
- `apply_patch` and core editing primitives remain active regardless of `[features]` settings.

### Google Anti Gravity

- `serverUrl` is the AGY HTTP field name; `url` is used by Ralph Workflow Workflow's internal upstream normalization.

## 4. How Ralph Workflow Verifies Enforcement

Ralph Workflow's test suite covers enforcement through agent invocation tests:

- **`tests/test_agents_invoke_1.py`** through **`tests/test_agents_invoke_5.py`** verify Claude, OpenCode, Codex, and AGY invocation enforcement.
- **`tests/test_agy_execution_contract.py`** proves AGY uses `AgyExecutionStrategy` with `supports_session_continuation()=False` and `supports_completion_enforcement()=True`, and that clean exit without `declare_complete` raises `AgentInvocationError` (non-retryable — no retry loop).
- **`tests/test_agy_runner_no_retry.py`** verifies that AGY missing-completion reaches `AGENT_FAILURE` via the `check_process_result` seam with exactly one invoke attempt (no retry loop), and that a completion-evidenced AGY run is accepted by the runner, returning `PipelineEvent.AGENT_SUCCESS`.
- **`tests/mcp/test_agy_transport.py`** verifies the AGY transport helpers, including `serverUrl` normalization for HTTP upstream servers.
- **`tests/test_agy_workspace_mcp.py`** verifies workspace-level MCP config injection/restore and that the written config is Ralph Workflow-only.
- **`tests/agents/test_invoke_mcp_merge.py`** verifies `invoke_agent()` writes and restores `.agents/mcp_config.json` with only the Ralph Workflow entry.

Transport selection and alias routing are verified in **`tests/test_agent_registry.py`**, which checks that `ccs` aliases resolve to the correct CLI and that each CLI receives the appropriate transport configuration.

Run these tests with:

```bash
cd ralph-workflow
pytest tests/test_agents_invoke_1.py tests/test_agents_invoke_2.py tests/test_agents_invoke_3.py tests/test_agents_invoke_4.py tests/test_agents_invoke_5.py tests/test_agy_workspace_mcp.py tests/agents/test_invoke_mcp_merge.py -q
```

## 5. Follow-Up

### MCP Reachability Preflight

With MCP-only enforcement active, agents that encounter an unreachable MCP server have no native fallback. Claude Code, OpenCode, Codex, and Google Anti Gravity will all produce output when their only available tools are unavailable, but that output will not be useful and may be silently wrong.

Ralph Workflow ships a built-in MCP reachability preflight via `ralph --check-mcp`. Running this command validates all configured MCP servers and exits with code 0 on success or non-zero on failure. The preflight is implemented in `ralph/cli/main.py` (`handle_check_mcp`) and calls `validate_custom_mcp_servers` from `ralph/pipeline/runner.py`, which probes each configured server for reachability. Running `ralph --check-mcp` before the first AGY (or any agent) run is the recommended way to catch unreachable MCP servers before they produce confusing agent failures.

### Prompt Size Monitoring

The Claude Code bug #32079 limitation (18 KB system prompt threshold) should be monitored. If Ralph Workflow's system prompt grows significantly, a check should be added to `invoke.py` that fails fast with a clear error rather than silently exiting.
