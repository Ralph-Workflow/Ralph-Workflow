# Agent CLI lifecycle

This page covers the full lifecycle of an agent CLI in Ralph Workflow:
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

You authenticate each agent CLI *yourself* before invoking Ralph Workflow.
Ralph Workflow then calls the agent CLI as-is and supervises the workflow.
It does not read, store, or proxy credentials.

> This is a deliberate trust boundary: **you** own your agent credentials.
> Ralph Workflow's job is to orchestrate work, not to handle secrets.

## Selection — the seven built-in agents

The canonical registry is `ralph/agents/builtin.py`. Ralph Workflow ships
with seven built-in agent specs that the bundled default policy can route
phases to:

| Built-in name     | CLI          | Transport            | Headless? | Use case                                              |
| ----------------- | ------------ | -------------------- | --------- | ----------------------------------------------------- |
| `claude`          | `claude`     | Interactive (PTY)    | Yes (with `--print`) | Anthropic's Claude Code; canonical reference agent |
| `claude-headless` | `claude`     | Headless subprocess  | Yes       | Same binary, no PTY — cheaper, less visibility        |
| `codex`           | `codex`      | Headless subprocess  | Yes       | OpenAI's Codex CLI                                     |
| `opencode`        | `opencode`   | Headless subprocess  | Yes       | Open-source terminal coding agent                     |
| `nanocoder`       | `nanocoder`  | Local TUI            | Yes       | Local-only TUI coding agent                          |
| `agy`             | `agy`        | Interactive (PTY)    | Yes (mock-backed) | Google's Antigravity CLI (v1.0.9+)              |
| `pi`              | `pi`         | Headless subprocess  | Yes       | Minimal coding agent; `pi --mode json <prompt>`       |

Beyond the seven built-ins, the registry resolves dynamic `<agent>/<model>`
aliases through `_resolve_dynamic_agent`. So `agy/Gemini 3.5 Flash (Medium)`
is a valid agent spec that resolves at runtime to the AGY binary with the
named model. The eight canonical `agy models` display names accepted by
`agy models` are:

- `Gemini 3.5 Flash (Medium)`
- `Gemini 3.5 Flash (High)`
- `Gemini 3.5 Flash (Low)`
- `Gemini 3.1 Pro (Low)`
- `Gemini 3.1 Pro (High)`
- `Claude Sonnet 4.6 (Thinking)`
- `Claude Opus 4.6 (Thinking)`
- `GPT-OSS 120B (Medium)`

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
differ by transport:

### Claude Code (interactive, PTY)

The Claude command builder emits the autonomy flag the bundled policy
declares, plus the session/resume and MCP config injection. With
`autonomy_mode = "dangerously-skip-permissions"`, the argv includes
`--dangerously-skip-permissions`. Claude's MCP config injection routes the
Ralph Workflow MCP tools into the agent's tool surface; see
[Advanced MCP Configuration](advanced-mcp-configuration.md).

### Claude Code (headless, no PTY)

Same binary, no PTY. Cheaper to spawn, less visibility. Use when you trust
the prompt-side artifact fallback and don't need live transcript display.

### Codex

The Codex builder uses OpenAI's `--approve` flag for unattended approval
plus any resume/session flags the policy declares.

### OpenCode

The OpenCode builder uses `--approve` for unattended approval plus
provider-specific flags forwarded through `--provider`.

### Nanocoder

Local-only TUI. The builder launches Nanocoder without autonomy flags —
Nanocoder has no remote auth surface.

### AGY (PTY)

The AGY builder runs `agy` inside a PTY with a bounded drain so buffered
stdout is captured end-to-end. The AGY parser classifies live output into
`text:` / `thinking:` / `tool_use:` events for the smoke report. With
`autonomy_mode = "dangerously-bypass-approvals-and-sandbox"`, the argv
includes the corresponding AGY-side flag.

### Pi

The Pi builder invokes `pi --mode json <prompt>` and parses the resulting
NDJSON stream per Pi's documented `AgentSessionEvent` vocabulary at
<https://pi.dev/docs/latest/json>. Pi has no documented CLI MCP wiring path,
so Ralph Workflow runs Pi **without** forwarding `RALPH_MCP_ENDPOINT`;
Pi workflow phases rely on the prompt-side artifact fallback instead of MCP
tool calls.

## End-to-end verification paths

Each agent has a documented verification path that targets its own contract:

- **Claude Code (interactive)**: `ralph smoke-interactive-claude`
- **AGY (interactive)**: `ralph smoke-interactive-agy` (mock-backed by default)
- **Codex, OpenCode, Nanocoder, Pi**: public-surface black-box pytest suite
  (`uv run pytest tests/agents/<agent>_blackbox.py -q`)

These suites verify Ralph Workflow's public registry / catalog / parser /
command-builder surface for each agent, plus the committed wire-format
fixture where applicable. They do **not** claim live MCP wiring for agents
that have no documented CLI MCP path.

The canonical end-to-end AGY verification (mock-backed, always green) is:

```bash
cd ralph-workflow && \
  RALPH_AGY_BINARY="$(pwd)/tests/_support/mock_agy.sh" \
  uv run python -m ralph smoke-interactive-agy --agent 'agy/Gemini 3.5 Flash (Medium)'
```

Expected green parity table excerpt:

```text
| Agent                         | Transport | File | Session                                       | Parser events | Tool activity | Artifact | Breaks |
| agy/Gemini 3.5 Flash (Medium) | agy       | yes  | interactive-agy-smoke-Gemini-3.5-Flash-Medium | 1             | yes           | yes      | none   |
```

## Completion and observability

Completion is evaluated from **durable evidence**, not from a conversational
vibe. Ralph Workflow expects each agent invocation to produce either a phase
artifact that satisfies the phase's declared contract, or an explicit
`declare_complete` MCP call. If a session exits **incomplete** (without
either signal), Ralph Workflow treats the work as incomplete rather than
calling it done — the session can be resumed, retried, or routed through the
next recovery path per policy.

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
   custom or mock AGY

For transport-specific issues, see [Troubleshooting](troubleshooting.md)
and the agent's verification path above.

## Related pages

- [Diagnostics](diagnostics.md) — pre-flight checks for agents and MCP
- [Configuration](configuration.md) — how phases are routed to agents
- [Agent Compatibility](agent-compatibility.md) — known caveats and
  workarounds per agent
- [CLI reference](cli.md) — every flag including `--list-agents`,
  `--diagnose`, and `--check-mcp`