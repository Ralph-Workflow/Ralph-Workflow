<!--
  Review note: rewrote the opening to lead with the canonical autopilot
  positioning language so the page agrees with the README and the manual
  home (rubric hard failure: surfaces fight each other). The "Brand-new
  here?" callout was kept on the same page so the test that ensures
  the page links to getting-started.md within the first 1000 characters
  still sees it.
-->

# Recovery

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) first. This page explains how Ralph Workflow behaves when a run is interrupted or something fails.

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

Ralph Workflow treats recovery as a built-in part of the product, not an afterthought. In normal use, you do not need to turn anything on. Ralph Workflow retries transient failures, keeps checkpoints up to date, and can resume safely after interruptions.

## The practical model

Most users only need to know three things:

- Ralph Workflow can retry some failures automatically
- Ralph Workflow writes checkpoints so a run can resume later
- you choose whether to resume from a saved checkpoint or start fresh

## Failure categories

Every failure is classified into one of four categories:

| Category | Description | Counts against budget? |
|----------|-------------|----------------------|
| `environmental` | Network outage, upstream service error, transport disconnect | No |
| `agent` | Empty output, idle timeout, malformed tool calls, repeated policy violations | Yes |
| `user_config` | Invalid config, unbound agent chain, missing required inputs | No |
| `ambiguous` | Ralph Workflow cannot confidently determine the cause | No |

The goal is simple: transient infrastructure problems should not burn the same budget as genuine agent failures.

## Offline detection and auto-resume

Ralph Workflow monitors connectivity. While offline, the run pauses instead of burning budget. When connectivity returns, Ralph Workflow resumes automatically.

You will see messages like:

```
Offline — paused (since HH:MM:SS)
```

and later:

```
Recovery resumed after offline
```

## Two-SIGINT behavior

- **First Ctrl+C** — cancel in-flight work, shut down in order, save the checkpoint, and pause
- **Second Ctrl+C** — exit immediately without waiting for cleanup

## Retry and fallover

Each phase uses an agent chain. If one agent exhausts its retry budget, Ralph Workflow can fall over to the next configured agent.

That is how longer unattended runs stay moving without being pinned to one provider.

## Recovery-cycle cap

`[general].max_cycles` limits how many full fallback cycles Ralph Workflow will attempt before stopping.

```toml
[general]
max_cycles = 3
```

This prevents a persistently failing workflow from retrying forever without making progress.

## Checkpoints

Ralph Workflow saves a checkpoint after each successful phase transition so a run can continue from the last completed step after an interruption or crash.

### Where the checkpoint lives

```
.agent/checkpoint.json
```

### What is stored

Typical checkpoint information includes:

- the current phase
- references to needed handoff artifacts
- consumed iteration counts
- the last error, when present

### Useful checkpoint commands

These are operator-side shell commands for the human running Ralph Workflow from a separate shell, not instructions for an in-session agent to spawn another `ralph` process recursively.

- From the human operator shell, `ralph --resume` continues from the saved checkpoint.
- From the human operator shell, `ralph --inspect-checkpoint` shows what would be resumed.
- From the human operator shell, `ralph --no-resume` ignores the checkpoint and starts fresh.

### Enabling or disabling checkpoints

```toml
[general.workflow]
checkpoint_enabled = true
```

When `checkpoint_enabled = false`, Ralph Workflow stops writing checkpoints and every run starts from the beginning.

## When to read the deeper internals

Most operators do not need the lower-level liveness rules, watchdog thresholds, or session-safety edge cases. If you are debugging those details specifically, use the maintainer-oriented architecture docs.

## Tool-availability failures

When the live MCP server reports that a tool is missing (the agent's
`tools/list` snapshot lost the alias after a restart, retry, or
transient recovery), the failure is classified as a tool-availability
failure and routed to a single bounded recovery path.

The recovery classifier matches on two surfaces:

- The literal substring `"no such tool available"` (case-insensitive)
  anywhere in the failure detail. This is the wire-level format
  Claude Code emits:
  `<tool_use_error>Error: No such tool available: mcp__<server>__<tool></tool_use_error>`.
- A runtime `ToolDispatchError` exception with the substring
  `"is not registered"` in its message. The class-name check
  excludes the programming-time `ToolRegistrationError` so bridge
  construction errors stay on the existing `USER_CONFIG` /
  `AMBIGUOUS` path.

The constant `ralph.recovery.failure_classifier._TOOL_AVAILABILITY_SUBSTRINGS`
contains exactly one entry: `"no such tool available"`. Do NOT add a
literal `"Tool ... is not registered"` substring here — the existing
matcher does case-insensitive literal-substring matching, not regex,
and the literal `...` would never match the runtime message.

When the classifier routes a failure to tool-availability, the
returned `ClassifiedFailure` has:

- `category = FailureCategory.AGENT`
- `reset_session = True`
- `reset_tool_registry = True`

The next attempt calls
`RestartAwareMcpBridge.reset_tool_registry()`, which rebuilds the
visible tool list by rerunning the preflight. The bridge's
`tool_registry_resets` counter is incremented by 1 per call. The
counter is exposed via the `tool_registry_resets` property so the
recovery controller and operator can inspect it.

### `_TOOL_REGISTRY_MAX_RESETS` cap

The new `_TOOL_REGISTRY_MAX_RESETS` constant (default 3) caps the
tool-registry-reset counter. After 3 successful resets, the next
`reset_tool_registry()` call raises `McpServerError` with a message
containing the substring `tool-registry-reset exhausted` and the
current count.

The cap is enforced at import time via `if/raise RuntimeError`, so
the constant cannot silently regress to zero or negative (it survives
`python -O`).

### Three additive caps

Three independent caps bound recovery retries. The orchestrator can
distinguish which one fired by the error message substring:

1. `tool-registry-reset exhausted` — the new
   `_TOOL_REGISTRY_MAX_RESETS` cap, raised by
   `RestartAwareMcpBridge.reset_tool_registry()` after 3
   tool-registry resets.
2. `restart budget` + `exhausted` — the existing
   `McpRestartPolicy.max_restarts` cap, raised by
   `RestartAwareMcpBridge.check_health_and_restart_if_needed()`
   after the configured number of crash restarts.
3. `recovery-attempt exhausted` — the existing `max_recovery_attempts`
   cap, raised by the recovery controller after the configured
   number of agent-invocation retries.

All three caps are independent. A misconfigured bridge can hit them
in any order. The error message substrings are stable, so the
operator can branch on them deterministically.

The `tests/test_recovery_three_caps_distinguished.py` test exercises
all three caps in sequence and asserts each raises a distinguishable
error message by substring.

## Related pages

- [Concepts](concepts.md) — phase, drain, checkpoint, and recovery terminology
- [Troubleshooting](troubleshooting.md) — common recovery-related issues and fixes
- [Parallel Mode](parallel-mode.md) — recovery behavior in same-workspace parallel runs
- [Developer Reference](developer-reference.md) — deeper implementation detail
- [MCP Architecture](mcp-architecture.md) — MCP server, tool registry, and dual-alias exposure

## Deterministic rc=0 classification

The `OpenCodeResumableExitError` (a clean `rc=0` exit with no
artifact, no `declare_complete` — see
`ralph.agents.invoke._open_code_resumable_exit_error`) is classified
deterministically as `FailureCategory.AGENT` by the explicit
typed-cause branch in `ralph/recovery/failure_classifier.py:_categorize_exc`
(lines 859-869). This branch precedes the broader
`AgentInvocationError` branch, so the exception NEVER falls to
`FailureCategory.AMBIGUOUS` and the operator never sees the noisy
`flagged_for_review=true` warning that the pre-fix code emitted.

The recovery action is decided by
`recovery_action_for_failure_reason(...)` in
`ralph/agents/invoke/_session_resume.py`:

- `has_prior_session=True` → returns `"resume"`. The recovery controller
  threads the captured `resumable_session_id` from the typed exception
  into `state.last_agent_session_id` (see
  `ralph/recovery/controller.py:690-692`), and the next attempt uses
  the per-transport resume flag (`--session <id>` for OpenCode,
  `--resume <id>` for Claude Code, etc.) to continue the existing
  session.
- `has_prior_session=False` → returns `"fresh"`. The next attempt
  starts a brand-new session via `fresh_session_options(opts)` which
  clears `session_id` to `None`.

Lock-in regression test: `tests/recovery/test_opencode_resumable_exit_classification.py`
covers the deterministic classification, the propagation of
`resumable_session_id` from the typed exception, and the resume/fresh
recovery action mapping.
