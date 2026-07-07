# Timeout policy

This document is the canonical home for the timeout and watchdog policy
that the runtime enforces. The conceptual overview lives on
[`../sphinx/watchdogs-and-timeouts.md`](../sphinx/watchdogs-and-timeouts.md);
this page is the policy-side reference.

## Workspace change kinds

The idle watchdog distinguishes between five kinds of workspace change
events, each weighted differently by the
`agent_workspace_change_weights` config key:

| Kind | Description |
|---|---|
| `source` | Source-code files (`.py`, `.rs`, `.ts`, etc.) |
| `log` | Log files written by the agent or the runtime |
| `cache` | Cache directories (e.g. `__pycache__`, `.mypy_cache`) |
| `artifact` | Build artifacts (e.g. `dist/`, `target/`, `build/`) |
| `other` | Anything not in the four categories above |

The full set of kinds and their default weights is in
`ralph/policy/defaults/recovery.toml`. Override per-project by setting
`agent_workspace_change_weights` in `ralph-workflow.toml`; the format
is `<kind>=<weight>` entries.

## Why this matters

The four-channel watchdog (`watchdogs-and-timeouts.md`) is the
high-level model. The timeout policy is the concrete knob set the
runtime enforces on top of that model. Both views are needed: the
mental model for design decisions, the policy reference for
configuration changes.

## Per-phase and per-iteration timeouts

Each phase declared in `pipeline.toml` carries a maximum wall-clock
duration. Each inner loop iteration has its own cap. Both are
policy-declared and enforced together with the watchdog.

## MCP call timeout

Every MCP operation has a bounded, fail-closed timeout (the **MCP
timeout contract**). The audit
(`ralph/testing/audit_mcp_timeout.py`) flags any blocking call
without a timeout.

## Recovery budget

The recovery budget is the maximum retries before the run declares
`budget-exceeded`. It is declared in
`ralph/policy/defaults/recovery.toml`.

## See also

- [Watchdogs and timeouts](../sphinx/watchdogs-and-timeouts.md) — the
  mental model page
- [Configuration](../sphinx/configuration.md) — the per-knob reference
