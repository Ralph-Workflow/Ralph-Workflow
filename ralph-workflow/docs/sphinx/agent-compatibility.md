# Agent compatibility

This reference lists the agent CLI integrations Ralph Workflow supports and the
important operational constraints for each one. It is a compatibility guide,
not a guarantee that every third-party CLI works in every environment.

## Supported agents

### AGY

- **CLI**: `agy`
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`; v1.1.8 publishes `--model` IDs and `--effort low|medium|high`. End-to-end model acceptance and effort effect are unverified pending manual probes.
- **Parser**: `generic` (native AGY parser; plain-text, not NDJSON)
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag; the earlier docs incorrectly attributed Codex's `--dangerously-bypass-approvals-and-sandbox` to AGY).
    - Completion contract: the durable `declare_complete` sentinel is always required; required-artifact phases also need the run-scoped artifact receipt, the same contract used by Claude interactive and headless Claude.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - Session continuation is unproven: the measured v1.1.8 CLI advertises continuation and conversation-resume flags, but a resume probe has not been run, so Ralph Workflow does not reuse AGY sessions.
    - `agy agents` reported no available agents on the measured stock v1.1.8 install. This is an install observation, not a universal capability claim; with `agent_subagents` and two or more work units, routing fails explicitly rather than falling back to sequential dispatch.
    - AGY is a supported orchestration path, not a replacement for Ralph Workflow.

```toml
[agents.agy]
cmd = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
can_commit = true
json_parser = "generic"
```

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
