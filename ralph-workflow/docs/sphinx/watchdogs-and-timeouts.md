# Watchdogs and timeouts

> **Mental model page.** This is explanation, not a how-to. The policy-side
> timeout declarations, the watchdog architecture, and the practical
> configuration path all live on this single page.

Ralph Workflow uses **watchdogs** to detect stuck or crashed agents and
**timeout policies** to bound how long any operation may take. Both are
policy-declared; the runtime enforces them.

## Why watchdogs matter for unattended runs

An unattended run by definition has no human in the loop. The runtime is
the only thing watching the agents. If an agent is stuck — looping,
waiting on input that never comes, crashed silently — the runtime must
detect and recover before the run wastes hours.

The original Ralph loop has no watchdog: it just keeps prompting until the
model says "done". Ralph Workflow replaces this with a structured watchdog
that consults policy.

## The four evidence channels

The current idle watchdog considers four evidence channels before declaring
a session idle:

| Channel       | What it watches                                                |
| ------------- | -------------------------------------------------------------- |
| `stdout`      | Agent stdout output (the baseline)                             |
| `mcp_tool`    | Ralph Workflow MCP tool calls and completions                  |
| `subagent`    | Delegated child progress, tool calls, and heartbeats           |
| `workspace`   | Workspace file changes from `WorkspaceMonitor`                 |

The watchdog verdict is based on **demonstrated work**, not mere existence.
An OpenCode subagent process that is alive but has produced no output, no
tool calls, and no file changes for the configured idle window is **not**
evidence of progress.

## Idle deferral

While any non-stdout channel is fresher than the
`agent_idle_activity_evidence_ttl_seconds` knob (under `[general]`, default
`30.0`), the `NO_OUTPUT_DEADLINE` fire is **deferred** and the watchdog
returns `CONTINUE`. Set the knob to `0.0` to opt out and restore the
legacy stdout-only behavior.

Workspace evidence collection runs whenever a run has a `workspace_path`,
regardless of whether the progress UI (`show_progress`) is enabled, so
quiet unattended runs that do real file work are not falsely killed.

## The HARD_STOP diagnostic

When the watchdog decides a session is stuck, it emits a `HARD_STOP`
diagnostic carrying a per-channel `evidence_summary` array with
`{channel, last_at, age_seconds, counter}` entries and an
`active_channel` label. The diagnostic tells a post-mortem reader exactly
which channels were fresh and which were stale at the moment of the
verdict.

Every deferred `CONTINUE` also carries the same `evidence_summary`, so a
reader can see why the watchdog chose to wait rather than kill.

## Absolute ceilings

Some ceilings are **absolute** — no activity can extend them:

- `SESSION_CEILING_EXCEEDED` — the maximum session duration
- `CHILDREN_PERSIST_TOO_LONG` — the cumulative waiting-on-child ceiling

These are checked **before** the deferral logic. No amount of fresh
evidence can override them.

## Timeout policy

The timeout policy is declared in `ralph/policy/defaults/recovery.toml` and
overridable per project. The runtime enforces:

- **Per-phase timeout** — each phase has a maximum wall-clock duration
- **Per-iteration timeout** — each inner loop iteration has its own cap
- **MCP call timeout** — every MCP operation has a bounded, fail-closed
  timeout (the **MCP timeout contract**)
- **Recovery budget** — the maximum retries before the run declares
  `budget-exceeded`

Per-phase and per-iteration timeouts are policy-declared and enforced
together with the watchdog. The MCP timeout contract is enforced
separately because it is a hard correctness invariant, not a tuning knob.

## Why bounded MCP timeouts are non-negotiable

An unbounded MCP call hangs the MCP server thread and starves the agent
of output. The `subprocess.run`/`.communicate`/`.wait` calls in
`ralph/mcp/` MUST carry a `timeout=` parameter, as must `httpx.*`,
`requests.*`, `urlopen`, and `socket.create_connection`. The only
bypass is an inline `# mcp-timeout-ok: <reason>` marker for a genuinely
unbounded-by-design call.

The audit (`ralph/testing/audit_mcp_timeout.py`) flags any blocking call
without a timeout. The audit runs under `make verify`, so a missing
timeout is a hard failure, not a warning.

## Recovery

When the watchdog or a timeout fires, the runtime hands control to the
recovery layer:

1. The watchdog emits the diagnostic and marks the session as
   `recoverable` or `non-recoverable`.
2. The recovery controller consults policy for the recovery budget.
3. If budget remains, the runtime retries the phase with the recovery
   prompt template.
4. If budget is exhausted, the run declares `budget-exceeded` and the
   terminal artifact is the most recent partial artifact.

See [Recovery](recovery.md) for the full recovery controller contract.

## Related pages

- [Recovery](recovery.md) — the recovery controller
- [MCP tools](mcp-tools.md) — the MCP timeout contract
- [Idle watchdog configuration](cli.md#idle-watchdog) — runtime flags
- [Verification model](verification-model.md) — what verification checks
  after recovery