# Agent compatibility

This reference lists the agent CLI integrations Ralph Workflow supports and the
important operational constraints for each one. It is a compatibility guide,
not a guarantee that every third-party CLI works in every environment.

## Model and provider syntax reference

Dynamic aliases select a model where the corresponding CLI integration supports
one. The emitted flags below are Ralph Workflow behaviour; third-party model
availability remains the CLI provider's responsibility.

| Alias family | Ralph Workflow emits | Constraint and example |
| --- | --- | --- |
| `claude/<model>` and `claude-headless/<model>` | `--model <model>` | A non-empty single model segment is required. |
| `codex/<model>[effort=<level>]` | `--model <model>` and, when selected, `-c 'model_reasoning_effort = "<level>"'` | Effort is `low`, `medium`, `high`, or `xhigh`. |
| `opencode/<model>` | `-m <model>` | All model path segments must be non-empty. |
| `nanocoder/<provider>[/<model>]` | `--provider <provider>` and optional `--model <model>` | The provider is required. |
| `agy/<published-id>[:effort]` | `--model <published-id>` and optional `--effort <effort>` | Example: `agy/gemini-3.6-flash-high:high`. The v1.1.8 probe accepted `gemini-3.6-flash-high --effort high`; aliases validate `low`, `medium`, and `high` before invocation. |
| `pi/<model>[:<thinking>]` | `--model <model>[:<thinking>]` | A bare model ID or slash-delimited provider/model path is accepted; empty path segments and ambiguous thinking suffixes are rejected. |
| `cursor/<model>` | `--model <model>` | `cursor/auto` selects Cursor's explicit Auto alias. |
| `ccs/<alias>` | The configured CCS alias command | Define the alias under `[ccs_aliases]`. |

## Supported agents

### Claude Code

- **CLI**: `claude`
- **Install / auth**: <https://docs.claude.com/claude-code>
- **Transport**: `claude-interactive`
- **Flags**: `--dangerously-skip-permissions`, `--verbose`, and `--resume {}`
- **Constraint**: Use `claude-headless` when a non-interactive streaming command is required.

### Claude headless

- **CLI**: `claude -p`
- **Install / auth**: <https://docs.claude.com/claude-code>
- **Transport**: `claude`
- **Flags**: `--print`, `--output-format=stream-json`, `--include-partial-messages`, `--permission-mode auto`, `--verbose`, and `--resume {}`
- **Constraint**: This is the built-in non-interactive Claude transport.

### Codex (OpenAI)

- **CLI**: `codex exec`
- **Install / auth**: <https://github.com/openai/codex>
- **Transport**: `codex`
- **Flags**: `--json` and `--dangerously-bypass-approvals-and-sandbox`
- **Constraint**: Codex has no Ralph-managed session-resume flag.

### OpenCode

- **CLI**: `opencode run`
- **Install / auth**: <https://opencode.ai>
- **Transport**: `opencode`
- **Flags**: `--format json` and `--session {}`
- **Constraint**: Dynamic aliases emit `-m <model>`.

### Nanocoder

- **CLI**: `nanocoder --mode yolo --no-plain run`
- **Install / auth**: <https://docs.nanocollective.org/nanocoder/docs>
- **Transport**: `nanocoder`
- **Flags**: `--mode yolo` and `--no-plain`
- **Constraint**: Dynamic aliases require a provider and may select a model.

### AGY

- **CLI**: `agy`
- **Install / auth**: <https://github.com/google-antigravity/antigravity-cli>
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`; v1.1.10 publishes model IDs and `--effort low|medium|high`. Live probes accepted both `gemini-3.6-flash-low` and `gemini-3.6-flash-high --effort high`.
- **Parser**: `agy` (`AgyParser`; maps `--output-format stream-json` events including `step_update` tool activity and multi-subagent updates, with plain-text fallback)
- **Wire format (measured against the live v1.1.10 binary)**: the full
  measured record, including the PTY capture method and the exact probe
  prompts, is tracked in `tests/display/_fixtures/agy_wire_provenance.md`.
  Summary:
    - `--print --output-format stream-json` writes stream-json frames to
      stdout **only when stdout is a controlling terminal (PTY)**; without a
      PTY the same invocation produces empty stdout.
    - Observed events: `init` (`model`, `cwd`, `tools`, `permission_mode`),
      `step_update` (`step_type` values `user_input`, `unknown`,
      `agent_response`, `tool`, `checkpoint`, `subagent`), `result`
      (`status`, `response`, `duration_seconds`, `num_turns`, `usage`), and an
      `error` emitter that exists in the binary but whose exact live payload
      shape was not captured.
    - The real clean-exit-with-no-output failure prints on stderr:
      `jetski: no output produced` because a tool's permission request was
      auto-denied in headless mode, naming `--dangerously-skip-permissions` /
      the `permissions.allow` setting as remediation; the CLI log
      (`~/.gemini/antigravity-cli/cli.log`) separately records
      `Print mode: timed out after N polls`. `ralph/agents/_agy_upstream_diagnostic.py`
      recognizes both patterns.
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag; the earlier docs incorrectly attributed Codex's `--dangerously-bypass-approvals-and-sandbox` to AGY).
    - Completion contract: AGY normally supplies the durable `declare_complete` sentinel; when its observed `--print` fallback produces a valid required artifact but misses the MCP completion call, Ralph Workflow writes the same host-owned durable sentinel after canonical promotion. Required-artifact phases still need the run-scoped receipt.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - Session continuation remains unavailable in Ralph Workflow. The v1.1.10 continuation and conversation-id probes accepted one-word `--print` prompts, but neither exposed the resumed-session identity; the integration therefore does not claim or reuse AGY sessions.
    - `agy agents` reported no available agents on the measured stock v1.1.10 CLI installation. This is a *subcommand listing* observation, not proof AGY lacks subagent capability: the live `init` frame's `tools` list includes `define_subagent`, `invoke_subagent`, and `manage_subagents`, and a live capture confirmed two subagents actually dispatched and completed in parallel through those tools. Ralph Workflow's own routing policy is unchanged by this correction: with `agent_subagents` and two or more work units, routing still fails explicitly rather than falling back to sequential dispatch.
    - The measured v1.1.10 mock live smoke exited 0 after creating its requested file, showing parser/tool activity without a permission prompt, and producing a valid fallback `smoke_test_result`. Ralph Workflow validated and canonically promoted it, recorded the receipt and host completion sentinel. `AgyParser` maps stream-json `init`, `step_update`, and `result` events.
    - AGY is a supported integration, not a replacement for Ralph Workflow.

```toml
[agents.agy]
cmd = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
can_commit = true
json_parser = "generic"

```

<a id="pi-pidev"></a>

### Pi (Pi.dev)

- **CLI**: `pi`
- **Install / auth**: <https://pi.dev/docs/latest/usage>
- **Transport**: `pi`
- **Flags**: `--mode json`, `--approve`, and `--session {}`
- **Constraint**: Ralph Workflow adds `--no-builtin-tools --extension <path>` when it configures Pi's MCP extension.

### Cursor (Cursor)

- **CLI**: `agent`
- **Install / auth**: <https://docs.cursor.com/agent>
- **Transport**: `cursor`
- **Flags**: `--print`, `--output-format stream-json`, `--stream-partial-output`, `--trust`, `--yolo`, `--approve-mcps`, and `--resume {}`
- **Constraint**: The emitted `--trust` and `--approve-mcps` flags cover headless workspace-trust and MCP approval.

<a id="ccs_aliases"></a>

### Claude Code Switch (CCS)

- **CLI**: a configured `ccs/<alias>` command
- **Transport**: `claude`
- **Flags**: `--print`, `--output-format=stream-json`, `--include-partial-messages`, `--permission-mode bypassPermissions`, `--verbose`, and `--resume {}`
- **Constraint**: CCS aliases are explicitly headless Claude commands configured under `[ccs_aliases]`. `ccs/<alias>` also resolves as a dynamic alias (like `claude/<model>`) even with no `[ccs_aliases]` entry at all.
- **Smoke test**: `ralph smoke-interactive-ccs --agent ccs/<alias>` (defaults to `ccs/glm`).
- **Caveats** (measured against the live CCS v8.9.0 CLI):
    - `--permission-mode auto` is a valid `claude` flag value but is rejected by
      `ccs`'s own pre-flight validator (`Invalid permission mode: "auto". Valid
      modes: default, plan, acceptEdits, bypassPermissions`) before the command
      ever reaches `claude` -- every `ccs/<alias>` invocation failed
      immediately with exit code 1. `bypassPermissions` is accepted by both
      the `ccs` wrapper and the underlying `claude` CLI and is the documented
      default.
    - Ralph Workflow never passes `ccs` its own `-p`/`--prompt` flag (only
      the long `--print` boolean plus the prompt text as a trailing
      `-- <text>` positional argument), so a `ccs/<alias>` invocation does
      NOT route through CCS's separate "headless executor" delegation-report
      feature (that feature is `-p "task"`-triggered only, a distinct
      one-shot "delegate and summarize" UX). The invocation instead takes
      CCS's raw pass-through path, which spawns `claude` and forwards its
      raw `--output-format=stream-json` NDJSON stdout untouched. A live run
      against `ccs/glm` confirmed parser events, tool activity, and artifact
      submission are all observed at WIRE/TRANSCRIPT provenance -- the same
      fidelity as the built-in `claude-headless` agent.
    - `AgentTransport.CLAUDE` (shared by `claude-headless` and every
      `ccs/<alias>`) previously raised `MissingCredentialsError:
      ANTHROPIC_API_KEY not set` before ever spawning `ccs` -- CCS profiles
      resolve their own provider credential per-profile (e.g. a GLM-backed
      profile injects `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` itself,
      confirmed via `ccs env <alias>`) and never need `ANTHROPIC_API_KEY` in
      Ralph Workflow's own environment. `ccs/<alias>` commands are now
      exempt from that preflight check.
    - The session-resume flow (`--resume {}`) requires the transport's
      `session_id` to survive the PTY line capture. A live `ccs/glm` run
      that otherwise passed every WIRE-level evidence check (file created,
      15 parsed events, tool activity, artifact submitted, completion
      observed) still reported "session ID was not observed" -- the `claude`
      transport's session-id extraction did not recover the id from CCS's
      PTY-interleaved stdout/stderr in that run, even though the id is
      present and independently extractable from the same raw `init` frame
      captured outside the PTY. This narrows retries/resume for
      `ccs/<alias>` without blocking a single-turn run; it is tracked as a
      follow-up, not fixed by this change.

### Built-in configuration examples

These examples are validated against the current agent configuration schema.

```toml
[agents.claude]
cmd = "claude"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
can_commit = true
session_flag = "--resume {}"
json_parser = "claude"

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

[agents.codex]
cmd = "codex exec"
output_flag = "--json"
yolo_flag = "--dangerously-bypass-approvals-and-sandbox"
can_commit = true
json_parser = "codex"

[agents.opencode]
cmd = "opencode"
output_flag = "--json-stream"
session_flag = "--session {}"
can_commit = false
json_parser = "opencode"

[agents.pi]
cmd = "pi"
output_flag = "--mode json"
yolo_flag = "--approve"
session_flag = "--session {}"
can_commit = true
display_name = "Pi"
json_parser = "pi"

[agents.nanocoder]
cmd = "nanocoder"
can_commit = false
json_parser = "generic"

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

```toml
[ccs]
print_flag = "--print"
output_flag = "--output-format=stream-json"
streaming_flag = "--include-partial-messages"
yolo_flag = "--permission-mode bypassPermissions"
verbose_flag = "--verbose"
session_flag = "--resume {}"
json_parser = "claude"
can_commit = true

[ccs_aliases]
glm = "ccs glm"
```
