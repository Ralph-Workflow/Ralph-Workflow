# Proposal: Live idle nag — prompt a frozen agent in place toward completion

- **Status:** Draft proposal (no implementation)
- **Date:** 2026-06-13
- **Scope target:** Option 3 — live injection across *all* transports (with an honest capability gate)
- **Trigger (decided):** reuse the watchdog `SUSPECTED_FROZEN` signal

## 1. Problem

When an agent stops producing output, the idle watchdog
(`ralph/agents/idle_watchdog/idle_watchdog.py`) eventually **fires and kills** the
process. Today there is no mechanism to *prod a still-living agent in place* —
to give it a fresh user turn that says "you appear idle; continue toward your
goal, or call `declare_complete`." The user's intent: ensure a frozen/stalled
agent is **prompted** rather than only killed, and that this works for **every**
agent, not just one transport.

## 2. What already exists (and why it is insufficient)

Ralph already has **two** "nag-ish" mechanisms. Neither prompts a frozen agent
in place.

### 2.1 Soft wrap-up nag — MCP tool-result banner

`ralph/mcp/server/_session_wrapup.py` + `_mcp_server.py:_maybe_append_wrapup_notice`.
Once a single invocation passes `agent_session_soft_wrapup_seconds` (default
`SESSION_SOFT_WRAPUP_SECONDS = 3000s`), **every `tools/call` result** gets a text
banner appended: *"⚠️ ~N min of your time budget remain. Finish up and call
declare_complete soon…"* Delivered over the Ralph MCP server, so it reaches
**every transport** uniformly.

**Why insufficient for "frozen":** a banner can only be *read when the agent makes
its next tool call*. A frozen agent making **no MCP calls** never receives it.
This is the user's exact objection: *"frozen agents are not making mcp calls."*

### 2.2 Restart nag — recovery retry prompt

`ralph/recovery/retry_prompt.py:build_retry_error_block`. After the watchdog fires
and the process is killed, the recovery controller relaunches the agent with an
*error-first* prompt block reminding it of the failure and the original task.

**Why insufficient:** this is a **between-iteration** re-prompt delivered by
*relaunching* — the agent is killed first. It is the universal floor, but it is
not an *in-place* nag.

### 2.3 The gap

> Deliver a new user turn to a **live, already-running** agent the moment it is
> suspected frozen — before resorting to kill+restart.

## 3. Key insight: execution model decides feasibility

Whether an in-place nag is even *possible* depends not on the transport's name but
on its **execution model**:

- **Session / interactive model** — the process runs a loop and has a
  *waiting-for-input state*. When the model finishes a turn it sits at a prompt.
  A new user turn can be delivered (PTY bytes, server API, or streaming-JSON
  stdin). **Live nag is possible.**
- **Single-shot / print model** — the CLI takes one prompt as an argument, runs
  the whole agentic loop to completion, and exits. There is **no input loop**.
  "Frozen" means either *still working* (will resume on its own) or *genuinely
  hung* (→ the watchdog kill is the only correct action). **Live nag is
  impossible**; bytes written to stdin go nowhere the model reads.

> **Engineering truth we must not paper over:** you cannot inject a turn into a
> single-shot process. Making more transports nag-able is not a Ralph trick — it
> requires *launching those transports in a session/interactive mode*, which is
> per-CLI work and is impossible for `GENERIC` by definition.

## 4. Per-transport capability matrix (researched)

Confidence + the launch-mode change required to enable a live nag. Items marked
**SPIKE** must be verified against the live CLI before building that transport's
injector (per `CLAUDE.md`: do not assume third-party behavior).

| Transport | Live nag possible? | Mechanism | Launch-mode change needed | Confidence |
|---|---|---|---|---|
| `CLAUDE_INTERACTIVE` | **Yes** | PTY master-fd write (`_pty_line_reader._write_pty_input`) | none — seam exists | High (in-repo) |
| `OPENCODE` | **Yes** | `opencode serve` HTTP API / ACP nd-JSON; `client.session.prompt(sessionId, parts)` | run via server/ACP session instead of one-shot | Med — SPIKE the exact send-to-running-session call |
| `CODEX` | **Yes, only in PTY/interactive** | long-lived PTY (`exec_command`+`write_stdin`); Enter=inject this turn, Tab=queue next | run codex interactively under a PTY, not `codex exec` | Med — `codex exec` reads stdin to completion, cannot stream; SPIKE PTY path |
| `CLAUDE` (headless `-p`) | **No via CLI** | one-shot; no streaming stdin input documented. Multi-turn-into-live-session needs Agent SDK / Managed Agents API | switch integration to SDK streaming-input (large) | High that CLI can't; SPIKE whether `--input-format stream-json` exists in installed version |
| `NANOCODER` | **Unknown** | — | — | SPIKE |
| `AGY` (`--print`) | **No** | one-shot print mode | none possible | Med |
| `GENERIC` | **No** | no transport contract | impossible by definition | High |

**Conclusion:** live injection is reachable through *different* mechanisms per
transport (PTY for interactive Claude & Codex; HTTP/ACP for OpenCode; SDK
streaming for programmatic Claude) and **impossible** for one-shot/print modes
and `GENERIC`. This is exactly what motivates a capability-gated abstraction with
a per-transport implementation, plus the kill+restart floor for the rest.

### Sources

- Claude Code headless / sessions: https://code.claude.com/docs/en/headless.md ,
  https://code.claude.com/docs/en/sessions.md
- Anthropic Managed Agents (mid-session events): https://platform.claude.com/docs/en/managed-agents/sessions.md
- OpenCode server / CLI: https://opencode.ai/docs/server/ , https://opencode.ai/docs/cli/
- Codex non-interactive / exec stdin: https://developers.openai.com/codex/noninteractive ,
  https://developers.openai.com/codex/cli/reference

## 5. Proposed architecture

### 5.1 Trigger — reuse `SUSPECTED_FROZEN` (decided)

The watchdog already emits `WaitingStatusKind.SUSPECTED_FROZEN` once per waiting
run at `agent_suspect_waiting_on_child_seconds`, *before* the hard
`CHILDREN_PERSIST_TOO_LONG` fire (`idle_watchdog.py:990`). It flows to the reader
via `_pty_line_reader._on_waiting_event` (line 158). The live nag hooks there.

> **Coverage caveat we must surface for the decision-maker.**
> `SUSPECTED_FROZEN` only fires on the **WAITING_ON_CHILD** branch (the agent
> spawned children and is quiet). The **pure-idle** path (no children, the model
> itself silent — e.g. an interactive agent that *finished its turn and is sitting
> at the prompt*, arguably the most common "frozen" case) currently goes
> drain-window → straight to `FIRE` with **no** pre-fire event. Reusing only
> `SUSPECTED_FROZEN` therefore does **not** cover the sit-at-prompt case. See
> §9 Open Question O1 — recommended fast-follow is a parallel pre-fire warning on
> the pure-idle path. Phase 1 honors the decision (SUSPECTED_FROZEN only) and
> documents the gap rather than silently missing it.

### 5.2 `LiveNagInjector` strategy (the core abstraction)

A small per-transport strategy resolved from the agent's `AgentTransport`:

```
LiveNagInjector (Protocol)
  supports_live_injection: bool        # capability gate
  inject(text: str) -> bool            # returns True if delivered; never raises
```

- `InteractiveClaudeInjector` — wraps the existing
  `_write_pty_input(self._input_writer_fd, text + "\r", lock=...)`.
- `OpenCodeSessionInjector` — POSTs to the running `serve`/ACP session
  (SPIKE the exact call).
- `CodexPtyInjector` — `write_stdin` into the long-lived codex PTY (SPIKE).
- `NullInjector` — `supports_live_injection = False`; used by `CLAUDE` headless,
  `AGY`, `NANOCODER` (until proven), `GENERIC`. `inject()` is a no-op returning
  `False`.

The injector is owned by the reader/runner that already holds the process handle,
constructed alongside the watchdog so it shares the process lifecycle.

### 5.3 Dispatch flow

```
watchdog SUSPECTED_FROZEN
  → _on_waiting_event(event)
    → if injector.supports_live_injection and not already_nagged_this_run:
          delivered = injector.inject(NAG_TEXT)
          record nag (once-per-waiting-run, like _suspicion_announced_for_run)
          log structured: {transport, delivered, idle_elapsed}
      else:
          no-op — the watchdog's existing FIRE → kill → retry_prompt floor applies
```

- **Once per waiting run.** Mirror `_suspicion_announced_for_run` so we nag at
  most once per stall episode; reset on `EXITED` / `record_activity()`.
- **Auto-clear is implicit.** If the agent resumes (produces output / makes
  progress), the watchdog leaves the waiting run and the flag resets — no banner
  to retract because the nag is a one-shot turn, not a sticky banner.

### 5.4 Nag content (decided earlier: generic continue/finish prod)

```
⚠️ You appear idle. If you are still working, continue toward your goal.
If you are finished, call declare_complete and stop. Do not restart from scratch.
```

Plain text; no goal/done-criteria tracking required for Phase 1.

### 5.5 Universal floor (what makes this "cover every agent")

Transports with `supports_live_injection = False` change **nothing** about today's
behavior: the watchdog still fires, the process is killed, and the recovery path
relaunches with `build_retry_error_block` (a goal-reminder prompt). So **every**
agent is *prompted toward completion* — **live** where the execution model allows,
**on-restart** where it does not. No agent is left un-prompted; the difference is
latency and whether the in-flight context survives.

## 6. Phased rollout

1. **Phase 1 — abstraction + interactive Claude.** Add `LiveNagInjector` protocol,
   `NullInjector`, `InteractiveClaudeInjector` (reuses existing PTY write), wire the
   `SUSPECTED_FROZEN` dispatch + once-per-run guard + structured log. Every other
   transport gets `NullInjector` (no behavior change). Fully testable in-repo.
2. **Phase 2 — OpenCode.** SPIKE the send-to-running-session call; launch opencode
   via server/ACP session; implement `OpenCodeSessionInjector`.
3. **Phase 3 — Codex.** SPIKE the PTY `write_stdin` path; run codex interactively
   under a PTY; implement `CodexPtyInjector`.
4. **Phase 4 — pure-idle pre-fire trigger (O1).** Add a pre-fire warning event on
   the no-children idle path so the sit-at-prompt case is nagged, not only the
   waiting-on-child case.
5. **Phase 5 (optional, large) — Claude headless via SDK streaming.** Only if the
   headless transport must gain live nag; this is an integration change, not a flag.
   `NANOCODER`/`AGY`/`GENERIC` remain on the kill+restart floor.

## 7. Risks & mitigations

- **Injecting into a busy/mid-generation session.** A "suspected frozen" agent may
  actually be slow, not idle. Gate strictly on the watchdog's existing
  suspect threshold; deliver at most once per run; the message is benign (a
  continue/finish reminder) so a spurious nag is low-harm. For Codex, prefer
  Tab-queue (next turn) over Enter-inject (current turn) semantics.
- **Double submission / prompt corruption (PTY).** Reuse the existing
  `_input_writer_lock` and the auto-response quiescence pattern so we never write
  into a mid-render TUI frame.
- **Launch-mode changes are invasive** (Phases 2–3 change how a transport is
  spawned → affects output parsing, permission handling, session lifecycle).
  Keep each behind its own phase + capability flag; default off until verified.
- **Over-claiming universality.** The matrix and the floor make the boundary
  explicit: `GENERIC` and one-shot CLIs are *kill+restart-prompted*, not
  live-nagged. The proposal must not be sold as "live nag for literally everyone."

## 8. Testing strategy (must honor `CLAUDE.md`)

- Black-box, no real subprocess / no `time.sleep` / no real I/O (60s combined
  budget, `audit_test_policy.py`).
- `FakeInjector` recording `inject()` calls; drive a synthetic `SUSPECTED_FROZEN`
  `WaitingStatusEvent` through `_on_waiting_event`; assert: injected once,
  not re-injected within the same run, re-armed after `EXITED`.
- Capability-gate test: `NullInjector` transports never inject and fall through to
  the existing FIRE path (assert behavior unchanged).
- Injector unit tests per transport behind their phase, using fakes for the PTY
  fd / HTTP client (no real CLI).

## 9. Open questions / decisions needed

- **O1 (recommended fast-follow):** add a pure-idle pre-fire warning event so the
  most common "model finished, sitting at prompt" freeze is live-nagged. Without
  it, Phase 1 only covers waiting-on-child stalls. *Decision: defer to Phase 4, or
  pull into Phase 1?*
- **O2:** for Codex, inject-this-turn (Enter) vs queue-next-turn (Tab) — default?
- **O3:** Claude headless — accept kill+restart floor permanently, or invest in the
  SDK streaming integration (Phase 5)?
- **O4:** should a successful live nag that *still* results in no progress shorten
  the remaining ceiling before the hard FIRE (escalation), or leave timing
  unchanged?

## 10. Out of scope

- Goal/done-criteria tracking in the nag text (Phase 1 uses a generic prod).
- Changing the absolute ceilings (`SESSION_CEILING_EXCEEDED`,
  `CHILDREN_PERSIST_TOO_LONG`) — those remain the backstop.
- The MCP tool-result banner channel (§2.1) — orthogonal; may later carry the same
  message for agents that *are* still calling tools.
