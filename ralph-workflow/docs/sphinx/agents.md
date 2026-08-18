# Agent CLI lifecycle

This page covers the full lifecycle of an agent CLI in Ralph Workflow, including bounded output summaries:
**selection**, **detection**, **authentication**, and **invocation**.
It complements [Configuration](configuration.md) (which configures each
phase to use an agent) and
[Agent Compatibility](agent-compatibility.md) (which lists every
supported agent with caveats and workarounds).

## The agent-CLI trust boundary

Ralph Workflow does **not** authenticate agent CLIs. Each agent CLI uses its
own native authentication:

- **Claude Code** — `claude login` / Anthropic API key in the local keychain
- **Codex CLI** — `codex login` / OpenAI API key in the local keychain
- **OpenCode** — provider-specific keys configured per provider
- **Nanocoder** — local-only TUI, no remote auth
- **Google Anti Gravity (AGY)** — `agy login` / Google account
- **Pi** — `pi` provider configuration
- **Cursor** — `agent login` / `CURSOR_API_KEY`

You authenticate each agent CLI *yourself* before invoking Ralph Workflow.
Ralph Workflow then calls the agent CLI as-is and supervises the workflow.
It does not read, store, or proxy credentials.

> This is a deliberate trust boundary: **you** own your agent credentials.
> Ralph Workflow's job is to orchestrate work, not to handle secrets.

## Selection — the nine built-in agents

The canonical registry is `ralph/agents/builtin.py`. Ralph Workflow ships
with nine built-in agent specs that the bundled default policy can route
phases to:

| Built-in name     | CLI          | Transport            | Headless? | Use case                                              |
| ----------------- | ------------ | -------------------- | --------- | ----------------------------------------------------- |
| `claude`          | `claude`     | Interactive (PTY)    | Optional via `claude-headless` | Anthropic's Claude Code; canonical reference agent |
| `claude-headless` | `claude`     | Headless subprocess  | Yes       | Same binary, no PTY                                   |
| `codex`           | `codex`      | Headless subprocess  | Yes       | OpenAI's Codex CLI                                     |
| `opencode`        | `opencode`   | Headless subprocess  | Yes       | Open-source terminal coding agent                     |
| `nanocoder`       | `nanocoder`  | Local TUI            | Yes       | Local-only TUI coding agent                          |
| `agy`             | `agy`        | Interactive (PTY)    | Manual paid diagnostic | Google's Antigravity CLI (v1.1.8 observed `gemini-3.6-flash-low`, high effort, stream-json `init`/`step_update`/`result`, and exit-0 fallback-artifact smoke path; re-measure after updates) |
| `pi`              | `pi`         | Headless subprocess  | Yes       | Minimal coding agent                                  |
| `cursor`          | `agent`      | Headless subprocess  | Yes       | Cursor Agent CLI; opt-in                              |
| `kimi`            | `kimi`       | Headless subprocess  | Yes       | Kimi Code CLI; `kimi/<model>` dynamic aliases (full configured `kimi-code/...` IDs) |

The registry resolves dynamic aliases. AGY v1.1.8 probes accepted exact published IDs such as `gemini-3.6-flash-low`; the measured CLI accepts `--effort` only without an explicit model, so Ralph Workflow rejects any `:<effort>` suffix on an `agy/<published-id>` alias before invocation. Use the full published ID, including any tier-encoded suffix such as `-low` or `-high`, as the model. Re-measure published IDs after AGY updates. Their syntax differs by agent;
use the complete [model and provider syntax reference](agent-compatibility.md#model-and-provider-syntax-reference)
rather than assuming one shared provider/model format. It includes every
built-in agent, a working example, and the literal CLI flags Ralph Workflow emits.

For chain and drain routing — using one agent's output as the next agent's
input across phases — see [Configuration](configuration.md).

## Detection — finding agents on PATH

Ralph Workflow discovers each agent CLI via `shutil.which(agent_binary)`.
Detection happens at the moment a phase is routed to an agent, not at
`--init` time, so a CLI you install between `ralph --init` and `ralph` is
picked up automatically.

To verify detection before a run:

```bash
ralph --list-agents
```

To validate availability alongside the rest of the pre-flight:

```bash
ralph --diagnose
```

### Overriding the binary path

Some agents allow pointing at a custom executable via environment variable.
The canonical example is `RALPH_AGY_BINARY`:

```bash
RALPH_AGY_BINARY=/path/to/custom/agy ralph --diagnose
```

The seam lives in `ralph/cli/commands/smoke.py` via
`_maybe_apply_agy_binary_override(agent_config)` immediately after
`registry.get(agent_name)`. The plumbing layer stays free of env-var seams;
the CLI surface applies the override at the boundary.

Cursor honors the same pattern via `RALPH_CURSOR_BINARY`:

```bash
RALPH_CURSOR_BINARY=/path/to/cursor-wrapper ralph --diagnose
```

The seam lives in `ralph/cli/commands/smoke.py` via
`_maybe_apply_cursor_binary_override(agent_config)`. Unlike AGY there
is no bundled mock binary for Cursor; the override points at a real
wrapper, alternate live binary, or an operator-wired test stub.

For mock-backed deterministic CI runs, point `RALPH_AGY_BINARY` at the
bundled mock:

```bash
RALPH_AGY_BINARY="$(pwd)/tests/_support/mock_agy.sh"
```

The mock entrypoint is `tests/_support/mock_agy.py` (run as
`python -m tests._support.mock_agy`); `tests/_support/mock_agy.sh` is a thin
shell wrapper suitable for `RALPH_AGY_BINARY`.

## Authentication — you own it

This section is short on purpose. **Ralph Workflow does not authenticate
agents.** Before your first run:

1. Install each agent CLI you want to use (e.g. `pipx install codex-cli`).
2. Authenticate each one using its native flow.
3. Verify the auth worked (e.g. `claude "say hello"` works from your shell).
4. Then run `ralph --diagnose` to confirm Ralph Workflow can find the CLI on
   `PATH`.

If `ralph --diagnose` reports the agent is missing but the CLI works in your
shell, the most common cause is that `PATH` in your non-interactive shell
differs from your interactive shell. Always test from the same shell type
you'll launch `ralph` from.

## Invocation — per-transport command builders

Each transport has a `CommandBuilder` in `ralph/agents/invoke/_command_builders/`
that assembles the argv passed to the agent subprocess. The argv shapes
differ by transport; the per-agent flag inventory lives in one place only —
the [model and provider syntax reference](agent-compatibility.md#model-and-provider-syntax-reference)
plus the per-agent `TOML` examples in [Agent Compatibility](agent-compatibility.md).
This page documents the *plumbing* of how each command builder fits the
runtime, not the flag values themselves.

### Claude Code (interactive, PTY)

The Claude command builder emits the autonomy flag the bundled policy
declares, plus the session/resume and MCP config injection. Claude's MCP
config injection routes the Ralph Workflow MCP tools into the agent's tool
surface; see [Advanced MCP Configuration](advanced-mcp-configuration.md).
For the exact flag values see the [Claude section in Agent
Compatibility](agent-compatibility.md#claude-code).

`claude` and `claude-headless` are both maintained invocation contracts. Do not
remove, deprecate, merge, alias, or silently redirect either one into the other
as part of unrelated agent work. A task about another agent is never a reason to
change either Claude contract.

### Claude Code (headless, no PTY)

Same binary, no PTY. Use when the documented non-interactive Claude path fits
the phase and you do not need live PTY transcript display. For the exact flag
values see the [Claude section in Agent
Compatibility](agent-compatibility.md#claude-code).

### Codex

The Codex command builder uses Codex's documented unattended-execution
flag (NOT the Claude `--dangerously-skip-permissions` flag — Codex has its
own). Codex has no Ralph-managed resume/session flag. For the exact flag
values see the [Codex section in Agent
Compatibility](agent-compatibility.md#codex-openai).

### OpenCode

The OpenCode command builder does NOT emit an autonomy flag; OpenCode ships
without a built-in unattended-execution mode in the bundled default policy.
Model selection uses `-m <provider>/<model>` when a model alias is
selected. For the exact flag values see the [OpenCode section in Agent
Compatibility](agent-compatibility.md#opencode).

### Nanocoder

Local-only TUI. The command builder launches Nanocoder without autonomy
flags — Nanocoder has no remote auth surface. Ralph Workflow keeps
Nanocoder on its PTY-backed Ink runtime because Nanocoder's JSON/plain
automation path has a hidden long-run action limit, observed around 100
actions. Do not switch Nanocoder to JSON/plain mode as the durable
backend. The command builder passes `--no-plain` before `run` to force the
Ink runtime. The maintained path must prove prompt submission,
parser-visible model text and tool activity, artifact completion, and
process cleanup through the Nanocoder smoke test.

### AGY (PTY)

The AGY command builder has a PTY integration path. v1.1.8 manual probes
observed the exact published ID `gemini-3.6-flash-low`, stream-json
`init`/`step_update`/successful `result` events, and an exit-0 smoke that
created a file and validated/promoted its fallback artifact before completion
evidence was recorded. The plain-text parser remains the smoke default;
stream-json is a separately observed CLI format. Session resume remains
unavailable because the continuation probes did not expose session identity.
See `tmp/agy-source-of-truth.txt` for the exact observations.

### Pi

The Pi command builder parses the resulting NDJSON stream per Pi's
documented `AgentSessionEvent` vocabulary at
<https://pi.dev/docs/latest/json>. Pi has no native MCP config file or
CLI flag, so Ralph Workflow materializes a per-run Pi extension and
launches Pi with `--no-builtin-tools --extension <generated file>` when
the Ralph Workflow MCP endpoint is available. The extension registers
Ralph Workflow MCP tools through Pi's custom-tool API and proxies calls
to the active HTTP MCP endpoint. Pi is session-capable in JSON mode: a
clean `rc=0` exit without the durable `declare_complete` sentinel, or
without the receipt for a required artifact, is retried against the
captured Pi session rather than treated as terminal success. For the exact
flag values see the [Pi section in Agent
Compatibility](agent-compatibility.md#pi-pidev).

### Cursor

The Cursor command builder parses the resulting NDJSON stream per
Cursor's documented `system` / `user` / `assistant` / `thinking` /
`tool_call` / `tool_result` / `result` envelope. Ralph Workflow wires MCP
through the documented `.cursor/mcp.json` (workspace-local) AND
`~/.cursor/mcp.json` (user-global) JSON files so the agent picks up the
endpoint regardless of the cwd it was launched from. The runtime
resolver restores the original bytes on exit so operator-managed MCP
servers are preserved across Ralph Workflow runs. For the exact flag
values see the [Cursor section in Agent
Compatibility](agent-compatibility.md#cursor-cursor).

## End-to-end verification paths

Each agent has a documented verification path that targets its own contract:

- **Claude Code (interactive)**: `ralph smoke-interactive-claude`
- **Nanocoder (interactive)**: `ralph smoke-interactive-nanocoder --agent '<exact nanocoder alias>'`
- **AGY (interactive)**: `ralph smoke-interactive-agy` (manual paid diagnostic)
- **Cursor (headless)**: `ralph smoke-interactive-cursor` (live binary required)
- **OpenCode (headless)**: `ralph smoke-interactive-opencode --agent '<exact opencode alias>'`
  (live binary required; see
  [the OpenCode section in Agent Compatibility](agent-compatibility.md#opencode)
  for the model/provider syntax reference and the live capture command)
- **Kimi (headless)**: `ralph smoke-interactive-kimi --agent kimi/kimi-code/kimi-for-coding`
  (live binary required; manual paid diagnostic outside `make verify`, like
  AGY. `kimi-code/kimi-for-coding` is the configured default standard model
  and the auditable least-cost choice per the dated cost table in
  [the Kimi section in Agent Compatibility](agent-compatibility.md#kimi-kimi-code).)

The Codex and Pi transports are exercised through the public-surface
black-box pytest suite (`uv run pytest tests/agents/parsers/ tests/test_opencode_display_fidelity.py -q`)
plus the per-agent parser unit tests under `tests/agents/parsers/`. There
is no `tests/agents/<agent>_blackbox.py` file; the older operator docs
referenced one that never existed.

These suites verify Ralph Workflow's public registry / catalog / parser /
command-builder surface for each agent, plus the committed wire-format
fixture where applicable. They do **not** claim live MCP wiring for agents
that have no documented CLI MCP path.

## Display-capability declarations

Every built-in agent carries a tri-state display-capability declaration
in its registration entry (see
:mod:`ralph.agents.display_capabilities`). The declaration names the
agent's stance on each catalog-derived display capability -- syntax
highlighting, file preview, and edit diff at minimum -- as one of:

- ``SUPPORTED`` -- the agent's parser produces a metadata envelope the
  shared preview builder recognizes, and ``build_edit_preview`` renders
  the surface. A SUPPORTED capability paired with an empty recorder
  during a smoke run is recorded as a defect.
- ``NOT_APPLICABLE (<reason>)`` -- the transport is structurally incapable
  of producing the surface (rare; almost always applicable agents).
- ``UNIMPLEMENTED (<reason>)`` -- the transport CAN produce the surface
  in principle, but the current parser/display wiring is not yet brought
  to the same shape as the working transports.

The declarations appear in
``.agent/artifacts/smoke_conformance_matrix.md`` (rendered by
:func:`ralph.pipeline.plumbing.smoke_plumbing.render_capability_matrix_markdown`)
alongside the per-run evidence-provenance grades. Read that file to see
which agent currently implements what. Adding a ninth built-in or a new
capability without declaring the full cross-product fails the gate that
``tests/agents/test_display_capabilities.py`` enforces.

AGY's v1.1.8 source record includes manual observations for the exact
published ID `gemini-3.6-flash-low`, stream-json `init`/`step_update`/`result`,
and the exit-0 smoke artifact/completion path. The continuation probes did not
establish a resumed-session identity, so the integration keeps AGY session reuse
disabled. The deterministic mock verifies the Ralph Workflow harness; the
source record preserves the separate live evidence.

A later v1.1.10 live-binary measurement (see the
[model and provider syntax reference](agent-compatibility.md#model-and-provider-syntax-reference)'s
AGY section and the git-tracked
`tests/display/_fixtures/agy_wire_provenance.md`) additionally observed the
full `step_update` `step_type` vocabulary (`user_input`, `unknown`,
`agent_response`, `tool`, `checkpoint`, `subagent`), the PTY requirement (no
controlling terminal means empty stdout), and confirmed that AGY's own tool
list includes `define_subagent` / `invoke_subagent` / `manage_subagents` --
two subagents were observed dispatched and completed in parallel through
those tools. A v1.1.13 re-measurement added the bodiless `system_message`
`step_type` (surfaced as a lifecycle event by `AgyParser`) and a
`workspace_uris` field on subagent entries, whose identity fields now
arrive already on the ACTIVE dispatch frame.

## Completion and observability

Completion is evaluated from **durable evidence**, not from a conversational
vibe. Every completion-enforced invocation requires the durable run-scoped
sentinel written by `declare_complete`. Required-artifact phases also require
the canonical run-scoped submission receipt; neither record is sufficient
alone. Optional-artifact and artifact-free phases relax only the receipt
requirement. If a session exits with incomplete evidence, Ralph Workflow
treats the work as incomplete rather than calling it done — the session can
be resumed, retried, or routed through the next recovery path per policy.

Interactive transports (Claude Code in PTY, AGY in PTY) give Ralph Workflow
better streaming **observability** into what the agent is doing during a
live session. Headless transports are cheaper to spawn and simpler to
automate, but the tradeoff is less step-by-step visibility while the run is
in flight. Pick the transport that matches the operational visibility you
need for the run.

Multimodal delivery is decided per session through
`ResolvedCapabilityProfile`, which acts as the pre-computed, session-owned
contract for how each modality is delivered to the active agent transport.

## When something doesn't work

If `ralph --diagnose` reports an agent problem, check:

1. The CLI is installed: `which <binary>` returns a path
2. The CLI works in your shell: `<binary> --version` succeeds
3. Auth is valid: try a one-shot prompt in your shell
4. PATH matches: launch `ralph` from the same shell type you tested in
5. The right binary override is set: `RALPH_AGY_BINARY` if you're using a
   custom or mock AGY; `RALPH_CURSOR_BINARY` if you're pointing Cursor at
   a wrapper or alternate live binary

For transport-specific issues, see [Troubleshooting](troubleshooting.md)
and the agent's verification path above.

## Related pages

- [Diagnostics](diagnostics.md) — pre-flight checks for agents and MCP
- [Configuration](configuration.md) — how phases are routed to agents
- [Agent Compatibility](agent-compatibility.md) — known caveats and
  workarounds per agent
- [CLI reference](cli.md) — every flag including `--list-agents`,
  `--diagnose`, and `--check-mcp`
