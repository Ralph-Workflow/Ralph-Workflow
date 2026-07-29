# Agent compatibility

This reference lists the agent CLI integrations Ralph Workflow supports and the
important operational constraints for each one. It is a compatibility guide,
not a guarantee that every third-party CLI works in every environment.

## Supported agents

### AGY

- **CLI**: `agy`
- **Transport**: `agy`
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`; measured v1.1.8 accepts published `--model` IDs and publishes `--effort low|medium|high` (end-to-end effort effect remains unverified).
- **Parser**: `generic` (native AGY parser; plain-text, not NDJSON)
- **Caveats**:
    - PTY-based runtime injection into the global `~/.gemini/antigravity-cli/mcp_config.json`, not manual pre-configuration. The injection writes only the Ralph Workflow entry and is restored on exit.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag; the earlier docs incorrectly attributed Codex's `--dangerously-bypass-approvals-and-sandbox` to AGY).
    - Completion contract: the durable `declare_complete` sentinel is always required; required-artifact phases also need the run-scoped artifact receipt, the same contract used by Claude interactive and headless Claude.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - Session continuation is unproven: v1.1.8 publishes `--continue` and `--conversation`, but a resume probe has not been run, so Ralph Workflow does not reuse AGY sessions.
    - `agy agents` reported no available agents on the measured stock v1.1.8 install. This is an install observation, not a universal capability claim; parallel plans use the existing sequential fallback unless a runtime exposes verified native delegation.
    - AGY is a supported orchestration path, not a replacement for Ralph Workflow.

```toml
[agents.agy]
```
