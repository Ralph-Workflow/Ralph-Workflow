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
| `claude/<model>` and `claude-headless/<model>` | `--model <model>` | A non-empty single model segment is required. Anthropic full model IDs (for example `claude-haiku-4-5-20251001`) and bracketed effort parameters (for example `claude/claude-haiku-4-5[effort=high]`) are accepted and tokenized as a single argv element. |
| `codex/<model>[effort=<level>]` or `codex/<model>[effort-<level>]` | `--model <model>` and, when selected, `-c 'model_reasoning_effort = "<level>"'` | Model IDs and effort levels have no release-specific allowlist. Effort tokens contain ASCII letters, digits, hyphens, or underscores; Codex validates availability. Example: `codex/gpt-6.1-sol[effort-low]`. |
| `opencode/<model>` | `-m <model>` | All model path segments must be non-empty. |
| `nanocoder/<provider>[/<model>]` | `--provider <provider>` and optional `--model <model>` | The provider is required. |
| `agy/<published-id>` | `--model <published-id>` | Example: `agy/gemini-3.6-flash-low`. AGY v1.1.8 accepts `--effort` only without an explicit model, so Ralph Workflow rejects any `:<effort>` suffix on an alias before invocation. Use the exact published ID, including any tier-encoded suffix such as `-low` or `-high`, as the model. |
| `pi/<model>[:<thinking>]` | `--model <model>[:<thinking>]` | A bare model ID or slash-delimited provider/model path is accepted; empty path segments and ambiguous thinking suffixes are rejected. |
| `cursor/<model>` | `--model <model>` | `cursor/auto` selects Cursor's explicit Auto alias. |
| `kimi/<model>` | `-m <model>` | The model must be a full configured ID from `~/.kimi-code/config.toml` (for example `kimi/kimi-code/kimi-for-coding`); bare unprefixed IDs are rejected by the CLI. |
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
- **Flags**: `print_flag = "--print"`, `yolo_flag = "--dangerously-skip-permissions"`; v1.1.10 publishes model IDs. The CLI accepts exact published IDs such as `gemini-3.6-flash-low`; it accepts `--effort` only without an explicit model, so Ralph Workflow does not emit `--model <id> --effort <level>`.
- **Parser**: `agy` (`AgyParser`; maps `--output-format stream-json` events including `step_update` tool activity and multi-subagent updates, with plain-text fallback)
- **Wire format (live v1.1.10 binary; v1.1.13 vocabulary)**: the full
  record, including the PTY capture method and the exact probe
  prompts, is tracked in `tests/display/_fixtures/agy_wire_provenance.md`.
  Summary:
    - `--print --output-format stream-json` writes stream-json frames to
      stdout **only when stdout is a controlling terminal (PTY)**; without a
      PTY the same invocation produces empty stdout.
    - Event kinds: `init` (`model`, `cwd`, `tools`, `permission_mode`),
      `step_update` (`step_type` values `user_input`, `unknown`,
      `agent_response`, `tool`, `checkpoint`, `subagent`; v1.1.13 adds the
      bodiless `system_message`), `result`
      (`status`, `response`, `duration_seconds`, `num_turns`, `usage`), and an
      `error` emitter that exists in the binary but whose exact payload
      shape is not documented. v1.1.13 also adds `workspace_uris` and emits
      each subagent entry's `conversation_id` / `log_uri` already on its
      ACTIVE dispatch frame; `AgyParser` surfaces any new bodiless
      `step_type` as a lifecycle event rather than dropping it.
    - The real clean-exit-with-no-output failure prints on stderr:
      `jetski: no output produced` because a tool's permission request was
      auto-denied in headless mode, naming `--dangerously-skip-permissions` /
      the `permissions.allow` setting as remediation; the CLI log
      (`~/.gemini/antigravity-cli/cli.log`) separately records
      `Print mode: timed out after N polls`. `ralph/agents/_agy_upstream_diagnostic.py`
      recognizes both patterns.
- **Caveats**:
    - Ralph Workflow gives each AGY invocation a private `HOME` containing generated `.gemini/antigravity-cli/mcp_config.json` and `.gemini/config/mcp_config.json` files. Concurrent sessions therefore use independent MCP endpoints without writing, locking, or restoring the operator's global configuration.
    - With `autonomy_mode = "dangerously-skip-permissions"`, the argv includes `--dangerously-skip-permissions` (AGY reuses the Claude flag).
    - Completion contract: the agent itself must call `declare_complete` to write the durable completion sentinel; the host writes no completion evidence for any transport. When AGY's `--print` fallback produces a valid required artifact but misses the MCP completion call, completion evidence is absent and the verdict is `DEGRADED (absent)`. Required-artifact phases still need the run-scoped receipt.
    - Multimodal delivery uses the Gemini provider profile.
    - The `RALPH_AGY_BINARY` env var is a general binary override. When it points at the deterministic mock at `tests/_support/mock_agy.sh` (basename starts with `mock_agy`) the harness takes the mock diagnostic path; any other executable override (a real wrapper, alternate live binary, or `agy` on `PATH`) takes the live diagnostic path and surfaces the upstream `~/.gemini/antigravity-cli/cli.log` quota or model-id diagnostic on empty stdout.
    - Session continuation remains unavailable in Ralph Workflow: the integration does not claim or reuse AGY sessions.
    - The `init` frame's `tools` list includes `define_subagent`, `invoke_subagent`, and `manage_subagents`; subagents dispatch and complete in parallel through those tools. Ralph Workflow's routing policy matches that transport: with `agent_subagents` and two or more work units, AGY parallel plans route through the same supported agent_subagents path as other native-subagent transports. The `agy agents` subcommand listing does not gate availability.
    - The mock smoke exits 0 after creating its requested file, shows parser/tool activity without a permission prompt, and produces a valid fallback `smoke_test_result`. Ralph Workflow validates and canonically promotes it and records the receipt; because AGY misses the `declare_complete` call, completion evidence is absent and the verdict is `DEGRADED (absent)` (the host does not synthesize completion evidence). `AgyParser` maps stream-json `init`, `step_update`, and `result` events.
    - AGY is a supported integration, not a replacement for Ralph Workflow.

```toml
[agents.agy]
cmd = "agy"
print_flag = "--print"
yolo_flag = "--dangerously-skip-permissions"
json_parser = "generic"

```

<a id="pi-pidev"></a>

### Pi (Pi.dev)

- **CLI**: `pi`
- **Install / auth**: <https://pi.dev/docs/latest/usage>
- **Transport**: `pi`
- **Flags**: `--mode json`, `--approve`, and `--session {}`
- **Native tools**: Ralph Workflow adds only `--extension <path>` for its MCP bridge. It does not pass `--no-builtin-tools`, and it preserves configured command arguments, so installed native extensions remain available. Pi's bundled `examples/extensions/subagent/` provides the official `subagent` tool. Install both `index.ts` and `agents.ts` under `~/.pi/agent/extensions/subagent/` plus its definitions under `~/.pi/agent/agents/`, or pass its `index.ts` through `--extension`. The developer prompt injects this setup path when the tool is absent; absence of the tool does not license implementing independent units in the parent session.

### Cursor (Cursor)

- **CLI**: `agent`
- **Install / auth**: <https://docs.cursor.com/agent>
- **Transport**: `cursor`
- **Flags**: `--print`, `--output-format stream-json`, `--stream-partial-output`, `--trust`, `--yolo`, `--approve-mcps`, and `--resume {}`
- **Credentials**: Cursor Agent uses ambient `CURSOR_API_KEY` or file-backed `~/.config/cursor/auth.json` (including `$XDG_CONFIG_HOME/cursor/auth.json`) or `~/.cursor/auth.json`. When neither valid file exists, Ralph Workflow extracts a valid IDE login from Cursor's local state database and writes it to both private file-store paths. Every private runtime coerces `AGENT_CLI_CREDENTIAL_STORE` to `file`, except an explicit `memory` value, and removes inherited `SSH_CLIENT`, `SSH_TTY`, and `SSH_CONNECTION` markers before spawning Cursor. Unattended macOS and Linux runs therefore use projected credentials without selecting a Keychain or showing an authentication dialog. Endpoint runs replace `mcp.json` with the run-scoped endpoint.
- **Concurrency**: Private homes keep each run's `mcp.json` independent. Large session directories such as `chats/` and `projects/` remain symlinked to the operator state so `--resume` works across concurrent runs; Ralph Workflow sidecar lock files are excluded.
- **Failure behavior**: Ralph Workflow raises `MissingCredentialsError` before launch when neither `CURSOR_API_KEY` nor either projected auth file is available.
- **Constraint**: The emitted `--trust` and `--approve-mcps` flags cover headless workspace-trust and MCP approval.

### Kimi (Kimi Code)

- **CLI**: `kimi` (v0.36.1)
- **Install / auth**: <https://platform.kimi.ai> — OAuth login (`kimi` interactive) wires the `managed:kimi-code` subscription provider
- **Transport**: `kimi`
- **Flags**: `-p <prompt-file>`, `--output-format stream-json`, `-m <model>` via the `kimi/<model>` alias, and `-S <session-id>` for session resume (`-S, --session [id] Resume a session`). The `-r` spelling is not a v0.36.1 option and is never emitted.
- **Constraint**: `-m` must carry a full configured ID from `~/.kimi-code/config.toml` (configured IDs: `kimi-code/kimi-for-coding`, `kimi-code/kimi-for-coding-highspeed`, `kimi-code/k3`, `kimi-code/k3-256k`; config default `kimi-code/kimi-for-coding`). A bare `-m kimi-for-coding` fails with `Model "kimi-for-coding" is not configured in config.toml`.
- **Multimodal**: image input is covered by the `image_in` capability on the authenticated models.
- **Native orchestration**: current Kimi Code exposes `Agent`, `TaskOutput`, `TaskWait`, and `TaskStop`; its experimental fork mode is a separate optional facility. Ralph Workflow does not disable native Kimi tools. An installed build that lacks those tools must be updated or must report the concrete version blocker; it must not silently implement ready work in the parent session.
- **Smoke**: `ralph smoke-interactive-kimi --agent kimi/kimi-code/kimi-for-coding` (the default alias).
- **Model cost comparison** (official model table at <https://www.kimi.com/code/docs/en/kimi-code/models>; availability and quota notes as stated there):

  | Model ID | Availability | Context | Stated quota note |
  | --- | --- | --- | --- |
  | `kimi-code/kimi-for-coding` | All members | 256k | none (baseline) |
  | `kimi-code/kimi-for-coding-highspeed` | Allegretto and above | 256k | 3x quota usage |
  | `kimi-code/k3` | Moderato and above (1M context needs Allegretto) | up to 1M | about 2x `k3-256k` consumption |
  | `kimi-code/k3-256k` | Moderato and above | 256k | none stated |

  The table publishes no direct `kimi-for-coding` vs `k3-256k` price,
  so the smoke default is the configured standard model
  `kimi-code/kimi-for-coding`: it is available to every member tier and
  carries no documented quota multiplier. Selection rule: pick the
  accessible model with the lowest explicitly documented multiplier and
  break missing-price ties in favor of the configured default, never a
  guess from the name.

<a id="ccs_aliases"></a>

### Claude Code Switch (CCS)

- **CLI**: a configured `ccs/<alias>` command
- **Transport**: `claude`
- **Flags**: `--print`, `--output-format=stream-json`, `--include-partial-messages`, `--permission-mode bypassPermissions`, `--verbose`, and `--resume {}`
- **Constraint**: CCS aliases are explicitly headless Claude commands configured under `[ccs_aliases]`. `ccs/<alias>` also resolves as a dynamic alias (like `claude/<model>`) even with no `[ccs_aliases]` entry at all.
- **Smoke test**: `ralph smoke-interactive-ccs --agent ccs/<alias>` (the requested target is `ccs/mm`; when unspecified it defaults to `ccs/glm`).
- **Caveats** (live CCS v8.9.0 CLI):
    - `--permission-mode auto` is a valid `claude` flag value but is rejected by
      `ccs`'s own pre-flight validator (`Invalid permission mode: "auto". Valid
      modes: default, plan, acceptEdits, bypassPermissions`) before the command
      ever reaches `claude` — the invocation exits with code 1.
      `bypassPermissions` is accepted by both
      the `ccs` wrapper and the underlying `claude` CLI and is the documented
      default.
    - Ralph Workflow never passes `ccs` its own `-p`/`--prompt` flag (only
      the long `--print` boolean plus the prompt text as a trailing
      `-- <text>` positional argument), so a `ccs/<alias>` invocation does
      NOT route through CCS's separate "headless executor" delegation-report
      feature (that feature is `-p "task"`-triggered only, a distinct
      one-shot "delegate and summarize" UX). The invocation takes
      CCS's raw pass-through path, which spawns `claude` and forwards its
      raw `--output-format=stream-json` NDJSON stdout untouched. Parser
      events, tool activity, and artifact submission are observed at
      WIRE/TRANSCRIPT provenance — the same fidelity as the built-in
      `claude-headless` agent.
    - The `ANTHROPIC_API_KEY` preflight in
      `ralph/agents/invoke/__init__.py::_fail_for_missing_credentials`
      applies to the `AgentTransport.CLAUDE` and
      `AgentTransport.CLAUDE_INTERACTIVE` transports, and is skipped when
      the command's first token is `ccs` or `claude`. Every `ccs/<alias>`
      command is exempt for that reason: CCS profiles resolve their own
      provider credential per-profile (e.g. a GLM-backed profile injects
      `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` itself, as shown by
      `ccs env <alias>`) and never need `ANTHROPIC_API_KEY` in
      Ralph Workflow's own environment. The built-in `claude-headless` agent is
      exempt as well — its command is `claude -p` — while a `claude`-transport
      agent whose command starts with neither `ccs` nor `claude` still hits
      the preflight.
    - Session identification: CCS aliases wrap headless Claude but the wrapper
      hides the session-id banner from the PTY capture, so Ralph Workflow
      declares `session_identifier_observable=False` for every `ccs/<alias>`.
      The smoke harness therefore does not require an observable session
      ID for `ccs/mm` or any other CCS alias; single-turn runs pass without
      retries/resume. The `--resume {}` flag is emitted, but session
      continuation remains best-effort for this transport.
    - Visible-output ceiling: because the resolved command carries
      `--output-format=stream-json`, `ccs/<alias>` is classified as
      `claude-headless` for the smoke harness's visible-output limit. This
      gives `ccs/mm` the same 250-line ceiling as built-in headless Claude
      rather than the 80-line interactive ceiling, so subagent runs are not
      spuriously failed on output volume.

## Delegation requirement

Ralph Workflow requires a coding agent with subagent and parallel-agent
support. Operators must choose and configure an agent that provides both.
Ralph Workflow's prompts assume this support unconditionally; the runtime does not detect
missing support, warn at prompt time, or provide a sequential fallback.
Configuring an agent without a subagent dispatch surface is an operator
misconfiguration: select a capable agent using the notes above.

The main agent delegates independent work, integrates the results, and runs
the full repository verification gate. Workers stay inside their assigned
directories, run focused checks, and return evidence to the main agent.

### Built-in configuration examples

These examples are validated against the current agent configuration schema.


```toml
[agents.claude]
cmd = "claude"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
session_flag = "--resume {}"
json_parser = "claude"

[agents.claude-headless]
cmd = "claude -p"
print_flag = "--print"
output_flag = "--output-format=stream-json"
streaming_flag = "--include-partial-messages"
yolo_flag = "--permission-mode auto"
verbose_flag = "--verbose"
session_flag = "--resume {}"
json_parser = "claude"

[agents.codex]
cmd = "codex exec"
output_flag = "--json"
yolo_flag = "--dangerously-bypass-approvals-and-sandbox"
json_parser = "codex"

[agents.opencode]
cmd = "opencode"
session_flag = "--session {}"
json_parser = "opencode"

[agents.pi]
cmd = "pi"
output_flag = "--mode json"
yolo_flag = "--approve"
session_flag = "--session {}"
display_name = "Pi"
json_parser = "pi"

[agents.nanocoder]
cmd = "nanocoder"
json_parser = "generic"

[agents.cursor]
cmd = "agent"
yolo_flag = "--yolo"
print_flag = "--print"
output_flag = "--output-format stream-json"
streaming_flag = "--stream-partial-output"
session_flag = "--resume {}"
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

[ccs_aliases]
glm = "ccs glm"
```
