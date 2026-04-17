# MCP Tool Restriction Guarantees

Ralph enforces MCP-only tooling by disabling native agent tools at the CLI and config layer. This document describes how that enforcement works for each supported CLI, known limitations, and how to verify it is active.

## 1. Overview

Ralph is designed to run agentic loops where every tool call produces an auditable action with a traceable identity. Native agent tools like `Read`, `Write`, `Edit`, and `Bash` bypass the MCP bridge and therefore break Ralph's audit trail, capability mapping, and policy enforcement.

Ralph's prompts claim "Native agent tools are DISABLED". This document describes how the CLI and config layer enforces that claim at invocation time for each supported backend:

- **Claude Code** receives a full empty-allowlist plus strict-MCP-config flags.
- **OpenCode** receives a config payload that explicitly sets each native tool to `false`.
- **Codex** receives a TOML config that disables several built-in features, but core editing primitives cannot be fully removed.

## 2. Per-CLI Guarantees

### Claude Code — Full Enforcement

Claude Code supports CLI flags that together remove all native tools from a session:

- `--tools ""` — An empty allowlist disables every native tool. The empty string is not a wildcard; it means "allow nothing".
- `--strict-mcp-config` — Ignores global and workspace MCP config files. Only the config explicitly passed via `--mcp-config` is used.
- `--allowedTools <explicit list>` — After clearing native tools, Ralph passes an explicit allowlist containing only Ralph MCP tool names (e.g., `mcp__ralph__read_file`).

The combination means Claude Code has no access to any tool that is not a Ralph MCP tool. There is no known escape hatch in the current version.

Reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference

### OpenCode — Full Enforcement

OpenCode reads configuration from a JSON object passed via the `OPENCODE_CONFIG_CONTENT` environment variable. Ralph builds this object in `_merge_opencode_config_content()` and disables all 16 native tools by setting each to `false`:

```
bash, codesearch, edit, glob, grep, list, lsp, patch, question, read, skill, task, todowrite, webfetch, websearch, write
```

The key mechanism is dict-spread merge:

```python
disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
config_obj["tools"] = {**existing_tools, **disable_overrides}
```

Because Ralph's disable entries come after the spread of existing user config, Ralph's `false` values win over any user-provided `tools.bash: true`. The MCP policy overrides user enables.

Reference: https://opencode.ai/docs

### Codex — Best-Effort Only

Codex is configured via a `config.toml` file. Ralph prepares this file in `_prepare_codex_home()` and sets the following in the `[features]` block:

```
features.shell_tool = false
features.multi_agent = false
features.undo = false
features.apps = false
web_search = "disabled"
```

These settings reduce the attack surface, but **`apply_patch` and core file-editing primitives cannot be disabled**. Codex has no comprehensive MCP-only mode and no `--tools` CLI flag equivalent. When an MCP endpoint is wired to Codex, Ralph logs a WARNING at every invocation:

```
Codex MCP wiring is best-effort; disabling built-in features for <endpoint>
```

Do not rely on Codex for environments that require strict tool isolation. Ralph's best-effort for Codex is explicitly logged as a warning at runtime.

Reference: https://platform.openai.com/docs/codex

## 3. Known Bugs and Limitations

### Claude Code

- **Bug #25589**: `--disallowedTools` ignores MCP tools when combined with `--mcp-config`. Ralph avoids this by using `--tools ""` instead of a disallowed-list approach.
- **Bug #13077**: `--allowedTools` wildcards do not match MCP tools. Ralph lists each Ralph tool name explicitly in `--allowedTools` rather than using a pattern.
- **Bug #32079**: `--tools ""` combined with `--mcp-config` and a system prompt larger than 18 KB causes Claude Code to exit silently. Ralph's system prompt is under 1 KB. If the prompt ever grows beyond 18 KB, this document must be updated and a mitigation applied.

### OpenCode

- The `OPENCODE_CONFIG_CONTENT` mechanism is verified empirically. OpenCode does not document an official API contract for config injection at startup. If a future OpenCode release changes how this is processed, enforcement may regress without warning.

### Codex

- No `--tools` CLI flag or equivalent exists for Codex. There is no native-tool-free mode.
- `apply_patch` and core editing primitives remain active regardless of `[features]` settings.
- The `web_search = "disabled"` string literal is required because Codex TOML interprets bare `disabled` as an identifier, not a string.

## 4. How Ralph Verifies Enforcement

Ralph's test suite covers enforcement through agent invocation tests:

- **`tests/test_agents_invoke.py`** contains `test_claude_native_tools_disabled()` which verifies the command-line flags are constructed correctly and that an empty tools allowlist is passed.
- The same file has `test_opencode_native_tools_disabled()` which verifies the JSON config payload contains `false` for all 16 native tool names.
- For Codex, `test_codex_features_disabled()` in the same file checks that the generated `config.toml` contains the expected feature-disabling entries.

Transport selection and alias routing are verified in **`tests/test_agent_registry.py`**, which checks that `ccs` aliases resolve to the correct CLI and that each CLI receives the appropriate transport configuration.

Run these tests with:

```bash
cd ralph-python
pytest tests/test_agents_invoke.py -v -k "native_tools_disabled or features_disabled"
```

## 5. Follow-Up

### MCP Reachability Preflight

With MCP-only enforcement active, agents that encounter an unreachable MCP server have no native fallback. Claude Code, OpenCode, and Codex will all produce output when their only available tools are unavailable, but that output will not be useful and may be silently wrong.

A preflight probe that verifies MCP server reachability before launching an agent is planned. See `.agent/PLAN.md` for the full roadmap. Until that probe is implemented, an unreachable MCP server will produce a confusing failure mode that is difficult to diagnose from logs alone.

### Prompt Size Monitoring

The Claude Code bug #32079 limitation (18 KB system prompt threshold) should be monitored. If Ralph's system prompt grows significantly, a check should be added to `invoke.py` that fails fast with a clear error rather than silently exiting.
