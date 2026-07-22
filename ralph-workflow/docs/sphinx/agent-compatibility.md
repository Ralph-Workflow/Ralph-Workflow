# Agent Compatibility Guide

This page documents the agent CLIs Ralph Workflow supports and the per-agent compatibility story.

Ralph Workflow's review phase is agent-agnostic in its prompts. Different agents differ in JSON output format, tool execution behavior, and CLI quirks, so each agent block below lists CLI, transport, parser, and known caveats. Some model providers (CCS/GLM, ZhipuAI, Qwen, DeepSeek) have weaker instruction-following; Ralph Workflow automatically applies the [Universal Review Prompt](#universal-review-prompt) for them. For best results, use Claude Code or Codex as the reviewer. Override with `--reviewer-agent claude` or `--reviewer-agent codex`.

## Built-in agents

Eight built-in agents ship with Ralph Workflow. The list below covers their CLI, transport, parser, and known caveats in a uniform shape.

### Model and provider syntax reference

Use the alias exactly as shown in an `[agent_chains]` list. The syntax is
agent-specific: Ralph Workflow does not translate one shared provider/model format.
The bundled chains work without any change; use this table only when choosing
which model an agent should run.

| Agent | Alias to type | Worked example | Literal CLI flag(s) Ralph Workflow emits |
| --- | --- | --- | --- |
| Claude Code (`claude`) | `claude/<model>` | `claude/opus` | `--model opus` |
| Claude Code headless (`claude-headless`) | `claude-headless/<model>` | `claude-headless/sonnet` | `--model sonnet` |
| Codex (`codex`) | `codex/<model>[effort=<level>]` | `codex/gpt-5.4[effort=high]` | `--model gpt-5.4 -c 'model_reasoning_effort = "high"'` |
| OpenCode (`opencode`) | `opencode/<provider>/<model>` | `opencode/openai/gpt-5.4` | `-m openai/gpt-5.4` |
| Nanocoder (`nanocoder`) | `nanocoder/<provider>/<model>` | `nanocoder/ollama/llama3.1` | `--provider ollama --model llama3.1` |
| Google Anti Gravity (`agy`) | `agy/<display name>` | `agy/Claude Sonnet 4.6 (Thinking)` | `--model 'Claude Sonnet 4.6 (Thinking)'` |
| Pi (`pi`) | `pi/<provider/model[:thinking]>` | `pi/anthropic/claude-sonnet-4:high` | `--model anthropic/claude-sonnet-4:high` |
| Cursor (`cursor`) | `cursor/<model id>` | `cursor/claude-opus-4-8[context=1m,effort=high,fast=false]` | `--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'` |

OpenCode is the only built-in agent that uses the short `-m` flag. Nanocoder
is the only one that sends provider and model as separate flags. Cursor keeps
its full model ID, including bracket parameters, unchanged.

### Claude Code

- **CLI**: `claude`
- **Transport**: `claude` (interactive) and `claude-headless`
- **Args**: `--json`, `--full-auto`, `--prompt <PROMPT>`, plus the autonomy flag the bundled policy declares. With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions`.
- **Parser**: `claude` (native, most reliable)
- **Caveats**: Claude's MCP config injection routes the Ralph Workflow MCP tools into the agent's tool surface; see [Advanced MCP Configuration](advanced-mcp-configuration.md). `claude` and `claude-headless` are both maintained invocation contracts. Do not remove, deprecate, merge, alias, or silently redirect either one into the other as part of unrelated agent work.

```toml
[agents.claude]
name = "claude"
command = "claude"
args = ["--json", "--full-auto", "--prompt", "<PROMPT>"]
json_parser = "claude"
```

### Codex (OpenAI)

- **CLI**: `codex`
- **Transport**: `codex`
- **Args**: `exec`, `--json`, `--full-auto`, `<PROMPT>`, plus `--approve` for unattended approval and any resume/session flags the policy declares.
- **Parser**: `codex` (native)

```toml
[agents.codex]
name = "codex"
command = "codex"
args = ["exec", "--json", "--full-auto", "<PROMPT>"]
json_parser = "codex"
```

### OpenCode

- **CLI**: `opencode`
- **Transport**: `opencode`
- **Args**: `--json`, `<PROMPT>`, plus `--approve` for unattended approval and `-m <provider>/<model>` when a model alias is selected.
- **Parser**: `opencode` (required, not interchangeable with the generic parser)

```toml
[agents.opencode]
name = "opencode"
command = "opencode"
args = ["--json", "<PROMPT>"]
json_parser = "opencode"
```

### Google Anti Gravity (AGY)

- **CLI**: `agy`
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`
- **Parser**: `generic` (native AGY parser; plain-text, not NDJSON)
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-bypass-approvals-and-sandbox"`, the argv includes the corresponding AGY-side flag.
    - Completion contract: `declare_complete` or phase artifact, same as Claude interactive.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - AGY is a supported orchestration path, not a replacement for Ralph Workflow.

```toml
[agents.agy]
name = "agy"
command = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
json_parser = "generic"
```

**MCP setup**: Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into AGY's global config file at `~/.gemini/antigravity-cli/mcp_config.json` before AGY launches and restores the original file after the run. Upstream MCP server definitions are read from both the workspace `.agents/mcp_config.json` and the global `~/.gemini/antigravity-cli/mcp_config.json`, normalised into a transport-neutral model, and re-exposed through Ralph Workflow's upstream proxy. See `ralph/mcp/transport/agy.py::agy_workspace_mcp_endpoint` for the implementation; run `ralph --check-mcp` to verify the wiring in your environment.

### Pi (pi.dev)

- **CLI**: `pi`
- **Transport**: `pi`
- **Args**: `--mode json`, `<PROMPT>`. Pi has no native MCP config file or CLI flag, so Ralph Workflow materializes a per-run Pi extension and launches Pi with `--no-builtin-tools --extension <generated file>` when the Ralph Workflow MCP endpoint is available.
- **Parser**: `pi` (NDJSON `AgentSessionEvent` per [pi.dev docs](https://pi.dev/docs/latest/json))
- **Caveats**:
    - `pi/<model>` shorthand preserves the full suffix (e.g. `pi/anthropic/claude-sonnet-4-20250514` becomes `--model anthropic/claude-sonnet-4-20250514`) using `name.removeprefix('pi/')` so multi-segment `provider/id` patterns round-trip intact.
    - Pi is session-capable in JSON mode: a clean `rc=0` exit without required artifact or completion evidence is retried against the captured Pi session rather than treated as terminal success.

### Nanocoder

- **CLI**: `nanocoder`
- **Transport**: `nanocoder`
- **Args**: Local-only TUI; the builder launches Nanocoder without autonomy flags. Ralph Workflow keeps Nanocoder on its PTY-backed Ink runtime by passing `--no-plain` before `run`.
- **Parser**: native (Nanocoder's TUI output)
- **Caveats**:
    - Do not switch Nanocoder to JSON/plain mode as the durable backend; the hidden long-run action limit around 100 actions would re-emerge.
    - Provider/model routing through the same direct-agent syntax used for OpenCode works (e.g. `nanocoder/ollama/llama3.1` resolves to `--provider ollama --model llama3.1`).

### Cursor (cursor)

- **CLI**: `agent`
- **Transport**: `cursor`
- **Flags**: `yolo_flag = "--yolo"`, `print_flag = "--print"`, `output_flag = "--output-format stream-json"`
- **Parser**: `generic`
- **Caveats**:
    - Headless `--print --output-format stream-json` is the documented automation API. The interactive Cursor TUI is not the default Ralph Workflow contract.
    - The `--trust` and `--approve-mcps` flags are emitted in extra-flags-before-prompt order so the agent does not block on the interactive workspace-trust and MCP-approval prompts.
    - `--yolo` is the documented autonomy flag for the headless transport. Operators who prefer the Smart-Auto alternative can override via `[agents.cursor].yolo_flag = "--auto-review"`.
    - Cursor is session-capable via the documented `--resume <chatId>` flag; the built-in `session_flag = "--resume {}"` wires the captured `session_id` into the next invocation.
    - The `RALPH_CURSOR_BINARY` env var is a general binary override (no bundled mock for cursor). The override points at a real wrapper, alternate live binary, or an operator-wired test stub. Non-executable paths are ignored with a WARNING.
    - Cursor's model catalog spans multiple upstream providers (OpenAI Codex variants, Claude variants, Composer, Auto, etc.); the resolver preserves the full id verbatim in the `--model` flag (including bracket parameterization like `claude-opus-4-8[context=1m,effort=high,fast=false]` and nested slash paths).

```toml
[agents.cursor]
name = "cursor"
command = "agent"
yolo_flag = "--yolo"
print_flag = "--print"
output_flag = "--output-format stream-json"
json_parser = "generic"
```

**MCP setup**: Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into Cursor's MCP config surface, which is documented as BOTH the workspace-local `.cursor/mcp.json` AND the user-global `~/.cursor/mcp.json`. Cursor may prefer one path over the other depending on the cwd it was launched from; writing to both ensures the agent picks up the MCP endpoint regardless of launch directory. On exit the original bytes are restored atomically (via `Path.replace`) so operator-managed MCP servers are preserved across Ralph Workflow runs. The merge respects the documented `unsafe_mode` semantics: in safe mode only the Ralph entry is written; in unsafe mode existing operator-managed servers are preserved alongside the Ralph entry. Upstream MCP server definitions are read from both `.cursor/mcp.json` (workspace-local) and `~/.cursor/mcp.json` (user-global), normalised into a transport-neutral model, and re-exposed through Ralph Workflow's upstream proxy. See `ralph/mcp/transport/cursor.py::cursor_workspace_mcp_endpoint` for the implementation; run `ralph --check-mcp` to verify the wiring in your environment.

### Generic / third-party agents

For third-party agents outside the eight built-ins (Aider, Gemini CLI, custom CCS aliases), use the `generic` parser and supply the agent's own flags:

```toml
[agents.aider]
name = "aider"
command = "aider"
args = ["--yes", "<PROMPT>"]
json_parser = "generic"
```

CCS (Claude Code Switcher) ALWAYS outputs Claude's stream-json format, regardless of which provider is in use (GLM, Gemini, etc.). The Claude parser is the correct parser for all CCS agents:

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

For weaker-instruction-following models (CCS/GLM, ZhipuAI/ZAI, Qwen, DeepSeek), the [Universal Review Prompt](#universal-review-prompt) is automatically applied. Aider uses a generic text-based output format; use the `generic` parser. The standalone `gemini` CLI is parsed by the `gemini` parser but is less mature than AGY.

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

## Contributing

Found an agent that should be in the list above? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the contribution path.

## Additional Resources

- **Main README**: [README.md](README.md)
- **Issue Tracker**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>