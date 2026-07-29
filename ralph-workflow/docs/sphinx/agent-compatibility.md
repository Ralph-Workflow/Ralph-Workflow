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
| `agy/<published-id>[:<effort>]` | `--model <published-id>` and optional `--effort <low\|medium\|high>` | Examples: `agy/gemini-3.6-flash-low`, `agy/gemini-3.6-flash-high:high`. An explicit effort suffix is rejected for IDs already ending in `-low`, `-medium`, or `-high`. On the measured AGY v1.1.8 install, both example forms were accepted by manual `--print` probes; the observable reasoning-quality effect of `--effort` is not established. |
| `pi/<model>[:<thinking>]` | `--model <model>[:<thinking>]` | A bare model ID or slash-delimited provider/model path is accepted; empty path segments and ambiguous thinking suffixes are rejected. |
| `cursor/<model>` | `--model <model>` | `cursor/auto` selects Cursor's explicit Auto alias. |
| `ccs/<alias>` | The configured CCS alias command | Define the alias under `[ccs_aliases]`. |

## Supported agents

### Claude Code

- **CLI**: `claude`
- **Transport**: `claude-interactive`
- **Flags**: `--dangerously-skip-permissions`, `--verbose`, and `--resume {}`
- **Constraint**: Use `claude-headless` when a non-interactive streaming command is required.

### Claude headless

- **CLI**: `claude -p`
- **Transport**: `claude`
- **Flags**: `--print`, `--output-format=stream-json`, `--include-partial-messages`, `--permission-mode auto`, `--verbose`, and `--resume {}`
- **Constraint**: This is the built-in non-interactive Claude transport.

### Codex (OpenAI)

- **CLI**: `codex exec`
- **Transport**: `codex`
- **Flags**: `--json` and `--dangerously-bypass-approvals-and-sandbox`
- **Constraint**: Codex has no Ralph-managed session-resume flag.

### OpenCode

- **CLI**: `opencode run`
- **Transport**: `opencode`
- **Flags**: `--format json` and `--session {}`
- **Constraint**: Dynamic aliases emit `-m <model>`.

### Nanocoder

- **CLI**: `nanocoder --mode yolo --no-plain run`
- **Transport**: `nanocoder`
- **Flags**: `--mode yolo` and `--no-plain`
- **Constraint**: Dynamic aliases require a provider and may select a model.

### AGY

- **CLI**: `agy`
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`; v1.1.8 publishes `--model` IDs and `--effort low|medium|high`. Manual probes accepted `gemini-3.6-flash-low` and `gemini-3.6-flash-high --effort high`; the effort's reasoning-quality effect is not observable from those probes.
- **Parser**: `generic` (native AGY parser; plain-text, not NDJSON)
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag; the earlier docs incorrectly attributed Codex's `--dangerously-bypass-approvals-and-sandbox` to AGY).
    - Completion contract: the durable `declare_complete` sentinel is always required; required-artifact phases also need the run-scoped artifact receipt, the same contract used by Claude interactive and headless Claude.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - Session continuation remains unavailable in Ralph Workflow: the measured v1.1.8 CLI advertises continuation and conversation-resume flags, but no probe has established that either resumes the intended prior session, so Ralph does not reuse AGY sessions.
    - `agy agents` reported no available agents on the measured stock v1.1.8 install. This is an install observation, not a universal capability claim; with `agent_subagents` and two or more work units, routing fails explicitly rather than falling back to sequential dispatch.
    - Live `smoke-interactive-agy` currently fails when AGY does not create the requested file, submit its artifact, or call `declare_complete`; the parity report names those missing signals. Do not select AGY for unattended work until this live integration defect is fixed.
    - AGY is a supported integration under active verification, not a replacement for Ralph Workflow.

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
- **Transport**: `pi`
- **Flags**: `--mode json`, `--approve`, and `--session {}`
- **Constraint**: Ralph Workflow adds `--no-builtin-tools --extension <path>` when it configures Pi's MCP extension.

### Cursor (Cursor)

- **CLI**: `agent`
- **Transport**: `cursor`
- **Flags**: `--print`, `--output-format stream-json`, `--stream-partial-output`, `--trust`, `--yolo`, `--approve-mcps`, and `--resume {}`
- **Constraint**: The emitted `--trust` and `--approve-mcps` flags cover headless workspace-trust and MCP approval.

<a id="ccs_aliases"></a>

### Claude Code Switch (CCS)

- **CLI**: a configured `ccs/<alias>` command
- **Transport**: `claude`
- **Flags**: `--print`, `--output-format=stream-json`, `--include-partial-messages`, `--permission-mode auto`, `--verbose`, and `--resume {}`
- **Constraint**: CCS aliases are explicitly headless Claude commands configured under `[ccs_aliases]`.

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
yolo_flag = "--permission-mode auto"
verbose_flag = "--verbose"
session_flag = "--resume {}"
json_parser = "claude"
can_commit = true

[ccs_aliases]
glm = "ccs glm"
```
