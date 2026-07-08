# Agent Compatibility Guide

This page documents which agent CLIs Ralph Workflow supports and the per-agent compatibility story.

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow's review phase is designed to be agent-agnostic in its prompts, but different agents may have varying levels of success due to differences in JSON output format, tool execution behavior, and agent-specific quirks.

> **⚠️ Compatibility Note**: GLM, ZhipuAI, Qwen, and DeepSeek agents have known compatibility issues with review tasks. Ralph Workflow automatically applies workarounds (Universal Review Prompt), but success rates may vary. **For best results, use Claude Code or Codex as the reviewer.** Override with `--reviewer-agent claude` or `--reviewer-agent codex`.

## Compatibility Matrix

| Agent | Developer Role | Reviewer Role | Notes |
|-------|---------------|---------------|-------|
| **Claude Code** | ✅ Excellent | ✅ Excellent | Best overall compatibility |
| **Codex (OpenAI)** | ✅ Excellent | ✅ Excellent | Great for security-focused reviews |
| **OpenCode** | ✅ Good | ✅ Good | Requires `opencode` parser |
| **Google Anti Gravity (AGY)** | ✅ Good | ✅ Good | First-class; PTY-based runtime injection with `~/.gemini/antigravity-cli/mcp_config.json` managed by Ralph Workflow |
| **Pi (pi.dev)** | ✅ Good | ✅ Good | Headless `--mode json`; Pi extension bridges MCP into the model tool surface |
| **Cursor** | ✅ Good | ✅ Good | Headless `--print --output-format stream-json`; MCP wired via `.cursor/mcp.json` / `~/.cursor/mcp.json` |
| **CCS/GLM** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **ZhipuAI/ZAI** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **Qwen** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **DeepSeek** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **Aider** | ✅ Good | ⚠️ Limited | Use `generic` parser |
| **Gemini CLI** | ✅ Good | ⚠️ Experimental | Parser support less mature |

### Legend

- ✅ **Excellent** - Works perfectly, recommended
- ✅ **Good** - Works well with minor caveats
- ⚠️ **Partial** - Works with automatic workarounds, may have reduced capability
- ⚠️ **Limited** - Works but output may be less structured
- ⚠️ **Experimental** - Not thoroughly tested

## Known Working Agents

### Claude Code (Recommended)

**Status**: ✅ Fully Compatible

**Configuration**:
```toml
[agents.claude]
name = "claude"
command = "claude"
args = ["--json", "--full-auto", "--prompt", "<PROMPT>"]
json_parser = "claude"
```

### Codex (OpenAI)

**Status**: ✅ Fully Compatible

**Configuration**:
```toml
[agents.codex]
name = "codex"
command = "codex"
args = ["exec", "--json", "--full-auto", "<PROMPT>"]
json_parser = "codex"
```

### OpenCode

**Status**: ✅ Compatible with Proper Configuration

**Configuration**:
```toml
[agents.opencode]
name = "opencode"
command = "opencode"
args = ["--json", "<PROMPT>"]
json_parser = "opencode"
```

### Google Anti Gravity (AGY)

**Status**: ✅ First-Class Supported Agent Path

**Configuration**:
```toml
[agents.agy]
name = "agy"
command = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
json_parser = "generic"
```

**MCP Setup**:
- Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into AGY's **global** config file at `~/.gemini/antigravity-cli/mcp_config.json` before AGY launches and restores the original file after the run.
- Upstream MCP server definitions are read from both the workspace `.agents/mcp_config.json` and the global `~/.gemini/antigravity-cli/mcp_config.json`, normalised into a transport-neutral model, and re-exposed through Ralph Workflow's upstream proxy.
- See `ralph/mcp/transport/agy.py::agy_workspace_mcp_endpoint` for the implementation; run `ralph --check-mcp` to verify the wiring in your environment.

**Notes**:
- Completion contract: `declare_complete` or phase artifact, same as Claude interactive
- Multimodal delivery uses the Gemini provider profile
- PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
- The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
- AGY is a supported orchestration path, not a replacement for Ralph Workflow

### Cursor (cursor)

**Status**: ✅ First-Class Supported Agent Path (8th built-in)

**Configuration**:
```toml
[agents.cursor]
name = "cursor"
command = "agent"
yolo_flag = "--yolo"
print_flag = "--print"
output_flag = "--output-format stream-json"
json_parser = "generic"
```

**MCP Setup**:
- Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP
  endpoint into Cursor's MCP config surface, which is documented as
  BOTH the workspace-local `.cursor/mcp.json` AND the user-global
  `~/.cursor/mcp.json`. Cursor may prefer one path over the other
  depending on the cwd it was launched from; writing to both
  ensures the agent picks up the MCP endpoint regardless of launch
  directory. On exit the original bytes are restored atomically
  (via `Path.replace`) so operator-managed MCP servers are
  preserved across Ralph Workflow runs. The merge respects the documented
  `unsafe_mode` semantics: in safe mode only the Ralph entry is
  written; in unsafe mode existing operator-managed servers are
  preserved alongside the Ralph entry.
- Upstream MCP server definitions are read from both
  `.cursor/mcp.json` (workspace-local) and `~/.cursor/mcp.json`
  (user-global), normalised into a transport-neutral model, and
  re-exposed through Ralph Workflow's upstream proxy. The
  provider-visible config only ever contains the Ralph Workflow
  MCP entry (in safe mode) or the Ralph entry plus operator-managed
  servers (in unsafe mode).
- See `ralph/mcp/transport/cursor.py::cursor_workspace_mcp_endpoint`
  for the implementation; run `ralph --check-mcp` to verify the
  wiring in your environment.

**Notes**:
- Headless `--print --output-format stream-json` is the documented
  automation API. The interactive Cursor TUI is NOT the default
  Ralph Workflow contract.
- The `--trust` and `--approve-mcps` flags are emitted in
  extra-flags-before-prompt order so the agent does not block on
  the interactive workspace-trust and MCP-approval prompts.
- `--yolo` is the documented autonomy flag for the headless
  transport. Operators who prefer the Smart-Auto alternative can
  override via `[agents.cursor].yolo_flag = "--auto-review"`.
- Cursor is session-capable via the documented `--resume <chatId>`
  flag; the built-in `session_flag = "--resume {}"` wires the
  captured `session_id` into the next invocation.
- The `RALPH_CURSOR_BINARY` env var is a general binary override
  (no bundled mock for cursor). The override points at a real
  wrapper, alternate live binary, or an operator-wired test stub.
  Non-executable paths are ignored with a WARNING.
- Cursor's model catalog spans multiple upstream providers (OpenAI
  Codex variants, Claude variants, Composer, Auto, etc.); the
  resolver preserves the full id verbatim in the `--model` flag
  (including bracket parameterization like
  `claude-opus-4-8[context=1m,effort=high,fast=false]` and nested
  slash paths). Model identity is recorded for multimodal and MCP
  planning; an unknown provider with the model id carried through
  is acceptable, fabrication is not.

## Agents with Known Issues

### CCS/GLM

**Status**: ⚠️ Partial Compatibility - Automatic Workarounds Applied

CCS agents require `print_flag = "--print"` in your `~/.config/ralph-workflow.toml`:

```toml
[ccs]
print_flag = "--print"
output_flag = "--output-format=stream-json"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
json_parser = "claude"
can_commit = true

[ccs_aliases]
glm = "ccs glm"
```

**Note:** CCS (Claude Code Switcher) ALWAYS outputs Claude's stream-json format, regardless of which provider you're using (GLM, Gemini, etc.). The Claude parser is the correct parser for all CCS agents.

### ZhipuAI / ZAI, Qwen / DeepSeek

**Status**: ⚠️ Partial Compatibility - Automatic Workarounds Applied

These models may have weaker instruction-following capabilities. Universal review prompt is automatically applied.

### Aider

**Status**: ⚠️ Limited Compatibility

Aider uses a generic text-based output format. Use the `generic` parser:

```toml
[agents.aider]
name = "aider"
command = "aider"
args = ["--yes", "<PROMPT>"]
json_parser = "generic"
```

### Gemini CLI

> **Note**: This section covers the standalone `gemini` CLI. Google Anti Gravity (AGY) is a separate Google coding CLI documented as a first-class supported agent path above.

**Status**: ⚠️ Experimental

```toml
[agents.gemini]
name = "gemini"
command = "gemini"
args = ["--json", "<PROMPT>"]
json_parser = "gemini"
```

## Agent Chain and Fallback Behavior

Ralph Workflow uses an **agent chain** system for fault-tolerant execution. When an agent fails, Ralph Workflow automatically falls back to the next agent in the chain.

### Agent Chain Configuration

Configure reusable named chains, then bind the built-in runtime drains in `~/.config/ralph-workflow.toml`:

```toml
[agent_chains]
developer = ["claude", "codex", "aider"]
reviewer = ["claude", "codex"]

[agent_drains]
planning = "developer"
development = "developer"
analysis = "developer"
review = "reviewer"
fix = "reviewer"
```

### Fallback Behavior by Role

| Runtime Drain | Binding | Fallback If Omitted |
|--------------|---------|--------------------|
| **Planning / Development / Analysis** | `agent_drains.* -> agent_chains.<name>` | Analysis inherits the resolved planning/development chain |
| **Review / Fix** | `agent_drains.* -> agent_chains.<name>` | Fix should usually share the review chain unless you want a dedicated fix chain |
| **Commit** | `agent_drains.commit -> agent_chains.<name>` | Inherits the resolved review/fix binding |

## JSON Parser Selection

| Parser | Best For | Notes |
|--------|----------|-------|
| `claude` | Claude Code | Native parser, most reliable |
| `codex` | OpenAI Codex | Native parser |
| `opencode` | OpenCode | Required for OpenCode |
| `gemini` | Gemini CLI | Native parser, experimental |
| `generic` | Any agent; Google Anti Gravity (AGY) | Native parser for AGY (plain-text, not NDJSON); fallback for other agents |

## Universal Review Prompt

The Universal Review Prompt is a simplified, agent-agnostic review prompt designed to work with AI models that have weaker instruction-following capabilities or known compatibility issues with complex structured prompts. Ralph Workflow automatically uses the Universal Review Prompt when the reviewer agent is `ccs/glm` (or any agent containing "glm"), ZhipuAI agents, Qwen agents, or DeepSeek agents.

Force the universal prompt with `RALPH_REVIEWER_UNIVERSAL_PROMPT=1` or add `force_universal_prompt = true` to `[general]` in `~/.config/ralph-workflow.toml`.

## How to use a different reviewer

The most reliable option is to use Claude Code or Codex as the reviewer while keeping GLM/CCS as the developer:

```bash
ralph --developer-agent ccs/glm --reviewer-agent claude
```

To skip review entirely:

```bash
RALPH_REVIEWER_REVIEWS=0 ralph
```

## Why Do Some Agents Fail?

### Technical Causes

1. **JSON Output Format Differences** - Different agents structure their JSON output differently. The `generic` parser can handle many variations but may miss some events.
2. **Tool Execution Behavior** - Review agents need to reliably produce the expected outputs in the configured format.
3. **Prompt Complexity Handling** - AI models vary in their ability to follow complex, multi-section prompts. The Universal Review Prompt simplifies instructions for models with weaker instruction-following.

### How Ralph Workflow Handles These Issues

1. **Universal Review Prompt** - Automatically activates for GLM, ZhipuAI, Qwen, and DeepSeek.
2. **Fast Fallback** - Known-problematic agents trigger quick fallback instead of retries.
3. **Error Classification** - Exit codes and stderr are analyzed to determine recovery strategy.

## Contributing

If you test Ralph Workflow with an agent not listed here, please contribute your findings by testing the agent with both development and review roles, documenting any issues encountered, sharing working configurations (anonymized), and submitting a PR to update this guide.

## Additional Resources

- **Main README**: [README.md](README.md)
- **Issue Tracker**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
