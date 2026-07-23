# Agent Compatibility Guide

This page documents the agent CLIs Ralph Workflow supports and the per-agent compatibility story.

Ralph Workflow's analysis phase is agent-agnostic in its prompts. Different agents differ in JSON output format, tool execution behavior, and CLI quirks, so each agent block below lists CLI, transport, parser, and known caveats. The agent that runs a given pipeline phase is selected by the policy-driven routing in `[agent_drains]`; see [Agent Chain and Fallback Behavior](#agent-chain-and-fallback-behavior) below for the canonical model. The reviewer / review-phase section of older docs is gone: the codebase removed the review-era CLI flags (`--reviewer-agent`, `--reviewer-model`, `--reviewer-reviews`, `--review-depth`), the `force_universal_prompt` config key, and the `RALPH_REVIEWER_*` environment variables. Pipeline routing is now driven entirely by `[agent_chains]` + `[agent_drains]` in `~/.config/ralph-workflow.toml`.

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
- **Flags**:
    - Interactive (`claude`): `cmd = "claude"`, `yolo_flag = "--dangerously-skip-permissions"`, `verbose_flag = "--verbose"`, `session_flag = "--resume {}"`, `can_commit = true`. No `output_flag` — interactive Claude streams via the PTY.
    - Headless (`claude-headless`): `cmd = "claude -p"`, `print_flag = "--print"`, `output_flag = "--output-format=stream-json"`, `streaming_flag = "--include-partial-messages"`, `yolo_flag = "--permission-mode auto"`, `verbose_flag = "--verbose"`, `session_flag = "--resume {}"`, `can_commit = true`.
- **Parser**: `claude` (native, most reliable)
- **Caveats**: Claude's MCP config injection routes the Ralph Workflow MCP tools into the agent's tool surface; see [Advanced MCP Configuration](advanced-mcp-configuration.md). `claude` and `claude-headless` are both maintained invocation contracts. Do not remove, deprecate, merge, alias, or silently redirect either one into the other as part of unrelated agent work. With `autonomy_mode = "dangerously-skip-permissions"` in interactive mode, the argv includes `--dangerously-skip-permissions`; in headless mode the same autonomy intent maps to `--permission-mode auto`.

```toml
# Interactive PTY transport — the canonical reference agent
[agents.claude]
cmd = "claude"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
can_commit = true
session_flag = "--resume {}"
json_parser = "claude"

# Headless subprocess transport — same binary, no PTY
[agents.claude-headless]
cmd = "claude -p"
print_flag = "--print"
output_flag = "--output-format=stream-json"
streaming_flag = "--include-partial-messages"
yolo_flag = "--permission-mode auto"
verbose_flag = "--verbose"
can_commit = true
session_flag = "--resume {}"
json_parser = "claude"
```

### Codex (OpenAI)

- **CLI**: `codex`
- **Transport**: `codex`
- **Flags**: `cmd = "codex exec"`, `output_flag = "--json"`, `yolo_flag = "--dangerously-bypass-approvals-and-sandbox"`, `can_commit = true`. Codex has no Ralph-managed resume/session flag and no `print_flag` — its subcommand `exec` is the only way to invoke it unattended.
- **Parser**: `codex` (native)
- **Caveats**: With `autonomy_mode = "dangerously-skip-permissions"` mapped to Codex, the argv includes `--dangerously-bypass-approvals-and-sandbox`. Note this is Codex's autonomy flag — it is NOT the Claude/AGY `--dangerously-skip-permissions` flag, despite the shared `autonomy_mode` value.

```toml
[agents.codex]
cmd = "codex exec"
output_flag = "--json"
yolo_flag = "--dangerously-bypass-approvals-and-sandbox"
can_commit = true
json_parser = "codex"
```

### OpenCode

- **CLI**: `opencode`
- **Transport**: `opencode`
- **Flags**: `cmd = "opencode"`, `output_flag = "--json-stream"`, `session_flag = "--session {}"`, `can_commit = false`. OpenCode has no `yolo_flag` (the bundled default policy ships OpenCode without an unattended-execution mode). Model selection uses `-m <provider>/<model>` when a model alias is selected (emitted by the OpenCode command builder, not declared in the agent config).
- **Parser**: `opencode` (required, not interchangeable with the generic parser)

```toml
[agents.opencode]
cmd = "opencode"
output_flag = "--json-stream"
session_flag = "--session {}"
can_commit = false
json_parser = "opencode"
```

### Google Anti Gravity (AGY)

- **CLI**: `agy`
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`
- **Parser**: `generic` (native AGY parser; plain-text, not NDJSON)
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag; the earlier docs incorrectly attributed Codex's `--dangerously-bypass-approvals-and-sandbox` to AGY).
    - Completion contract: `declare_complete` or phase artifact, same as Claude interactive.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - AGY is a supported orchestration path, not a replacement for Ralph Workflow.

```toml
[agents.agy]
cmd = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
can_commit = false
json_parser = "generic"
```

**MCP setup**: Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into AGY's global config file at `~/.gemini/antigravity-cli/mcp_config.json` before AGY launches and restores the original file after the run. Upstream MCP server definitions are read from both the workspace `.agents/mcp_config.json` and the global `~/.gemini/antigravity-cli/mcp_config.json`, normalised into a transport-neutral model, and re-exposed through Ralph Workflow's upstream proxy. See `ralph/mcp/transport/agy.py::agy_workspace_mcp_endpoint` for the implementation; run `ralph --check-mcp` to verify the wiring in your environment.

### Pi (pi.dev)

- **CLI**: `pi`
- **Transport**: `pi`
- **Flags**: `cmd = "pi"`, `output_flag = "--mode json"`, `yolo_flag = "--approve"`, `session_flag = "--session {}"`, `can_commit = true`, `display_name = "Pi"`. The `<PROMPT>` argument is emitted by the Pi command builder, not declared in the agent config. Pi has no native MCP config file or CLI flag, so Ralph Workflow materializes a per-run Pi extension and launches Pi with `--no-builtin-tools --extension <generated file>` when the Ralph Workflow MCP endpoint is available.
- **Parser**: `pi` (NDJSON `AgentSessionEvent` per [pi.dev docs](https://pi.dev/docs/latest/json))
- **Caveats**:
    - `pi/<model>` shorthand preserves the full suffix (e.g. `pi/anthropic/claude-sonnet-4-20250514` becomes `--model anthropic/claude-sonnet-4-20250514`) using `name.removeprefix('pi/')` so multi-segment `provider/id` patterns round-trip intact.
    - Pi is session-capable in JSON mode: a clean `rc=0` exit without required artifact or completion evidence is retried against the captured Pi session rather than treated as terminal success.

```toml
[agents.pi]
cmd = "pi"
output_flag = "--mode json"
yolo_flag = "--approve"
session_flag = "--session {}"
can_commit = true
display_name = "Pi"
json_parser = "pi"
```

### Nanocoder

- **CLI**: `nanocoder`
- **Transport**: `nanocoder`
- **Flags**: `cmd = "nanocoder"`, `can_commit = false`. Local-only TUI; the builder launches Nanocoder without autonomy flags. Ralph Workflow keeps Nanocoder on its PTY-backed Ink runtime by passing `--no-plain` before `run`. No `output_flag` / `yolo_flag` / `session_flag` are declared — Nanocoder has no unattended-execution mode in the bundled default policy.
- **Parser**: `generic` (native Nanocoder parser; the TUI output, not JSON)
- **Caveats**:
    - Do not switch Nanocoder to JSON/plain mode as the durable backend; the hidden long-run action limit around 100 actions would re-emerge.
    - Provider/model routing through the same direct-agent syntax used for OpenCode works (e.g. `nanocoder/ollama/llama3.1` resolves to `--provider ollama --model llama3.1`).

```toml
[agents.nanocoder]
cmd = "nanocoder"
can_commit = false
json_parser = "generic"
```

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
cmd = "agent"
yolo_flag = "--yolo"
print_flag = "--print"
output_flag = "--output-format stream-json"
streaming_flag = "--stream-partial-output"
session_flag = "--resume {}"
can_commit = true
display_name = "Cursor"
json_parser = "generic"
```

**MCP setup**: Ralph Workflow automatically injects the run-scoped Ralph Workflow MCP endpoint into Cursor's MCP config surface, which is documented as BOTH the workspace-local `.cursor/mcp.json` AND the user-global `~/.cursor/mcp.json`. Cursor may prefer one path over the other depending on the cwd it was launched from; writing to both ensures the agent picks up the MCP endpoint regardless of launch directory. On exit the original bytes are restored atomically (via `Path.replace`) so operator-managed MCP servers are preserved across Ralph Workflow runs. The merge respects the documented `unsafe_mode` semantics: in safe mode only the Ralph entry is written; in unsafe mode existing operator-managed servers are preserved alongside the Ralph entry. Upstream MCP server definitions are read from both `.cursor/mcp.json` (workspace-local) and `~/.cursor/mcp.json` (user-global), normalised into a transport-neutral model, and re-exposed through Ralph Workflow's upstream proxy. See `ralph/mcp/transport/cursor.py::cursor_workspace_mcp_endpoint` for the implementation; run `ralph --check-mcp` to verify the wiring in your environment.

### Generic / third-party agents

For third-party agents outside the eight built-ins (Aider, Gemini CLI, custom CCS aliases), use the `generic` parser and supply the agent's own flags:

```toml
[agents.aider]
cmd = "aider"
json_parser = "generic"
# Aider's own --yes flag and `<PROMPT>` argument are emitted by the
# command builder from the registered parser/strategy pair; the
# AgentConfig model only declares cmd + parser, not raw argv.
```

CCS (Claude Code Switcher) ALWAYS outputs Claude's stream-json format, regardless of which provider is in use (GLM, Gemini, etc.). The Claude parser is the correct parser for all CCS agents:

```toml
[ccs]
print_flag = "--print"
output_flag = "--output-format=stream-json"
streaming_flag = "--include-partial-messages"
yolo_flag = "--permission-mode auto"
verbose_flag = "--verbose"
session_flag = "--resume {}"
json_parser = "claude"
can_commit = true

[ccs_aliases]
glm = "ccs glm"
```

For weaker-instruction-following models (CCS/GLM, ZhipuAI/ZAI, Qwen, DeepSeek), the analysis prompt is rendered with a simplified, agent-agnostic shape so those models follow the structured artifacts reliably. Aider uses a generic text-based output format; use the `generic` parser. The standalone `gemini` CLI is parsed by the `gemini` parser but is less mature than AGY.

```toml
[agents.gemini]
cmd = "gemini"
json_parser = "gemini"
# The Gemini CLI's own --json flag and `<PROMPT>` argument are emitted
# by the command builder from the registered parser/strategy pair.
```

## Agent Chain and Fallback Behavior

Ralph Workflow uses a policy-driven routing system for fault-tolerant execution: each named `[agent_chains.*]` entry is an ORDERED FALLBACK LIST of agents for a role (tried left-to-right until one succeeds), and each `[agent_drains.*]` entry is a ROUTING LABEL that binds a pipeline phase to one of those chains.

### Agent Chain Configuration

Configure reusable named chains, then bind the bundled runtime drains in `~/.config/ralph-workflow.toml`. The drain names below are the ones the bundled pipeline actually reads; each value must name a key in `[agent_chains]` (or be added there if you extend the policy):

```toml
[agent_chains]
planning = ["claude/opus"]
development = ["claude/sonnet"]
analysis = ["claude/opus"]
commit = ["claude/haiku"]

[agent_drains]
planning = "planning"
planning_analysis = "analysis"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
analysis = "analysis"
commit = "commit"
```

The bundled defaults ship with only the `claude` agent active so a first-run user has a satisfiable configuration out of the box. To add `opencode` once it is on `PATH`, extend the relevant chain list (`development = ["claude/sonnet", "opencode/openai/gpt-5.4"]`). To add `codex`, append it the same way. CCS aliases are configured under `[ccs_aliases]` — see [Configuration Reference](configuration.md#ccs_aliases) for the CCS plumbing.

### Fallback Behavior by Role

| Runtime Drain | Binding | Fallback If Omitted |
|--------------|---------|--------------------|
| `planning` / `planning_analysis` | `agent_drains.* -> agent_chains.<name>` | The policy load fails `validate_chain_exists` if the named chain key is missing — there is no silent default. |
| `development` / `development_analysis` / `development_commit` | `agent_drains.* -> agent_chains.<name>` | Same `validate_chain_exists` failure as above. |
| `analysis` | `agent_drains.* -> agent_chains.<name>` | Same `validate_chain_exists` failure as above. |
| `commit` | `agent_drains.* -> agent_chains.<name>` | Same `validate_chain_exists` failure as above. |

## JSON Parser Selection

| Parser | Best For | Notes |
|--------|----------|-------|
| `claude` | Claude Code | Native parser, most reliable |
| `codex` | OpenAI Codex | Native parser |
| `opencode` | OpenCode | Required for OpenCode |
| `gemini` | Gemini CLI | Native parser, experimental |
| `generic` | Any agent; Google Anti Gravity (AGY) | Native parser for AGY (plain-text, not NDJSON); fallback for other agents |

## Contributing

Found an agent that should be in the list above? See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the contribution path.

## Additional Resources

- **Main README**: [README.md](../../README.md)
- **Issue Tracker**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>