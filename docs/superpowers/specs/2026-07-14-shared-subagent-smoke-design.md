# Shared Subagent Smoke Scenario

## Purpose

Add an operator-selectable subagent scenario to the shared interactive-agent
smoke harness. The scenario is for diagnosing subagent dispatch, transcript
parsing, activity routing, watchdog decisions, and progress after a subagent
tool result without changing the default smoke run.

## Interface

Every interactive smoke command accepts `--subagents`. Without the option, the
existing prompt, checks, report, and exit status remain unchanged. With the
option, the shared prompt asks the agent to delegate one bounded, read-only
task to a native subagent before the main agent creates the existing todo-list
artifact.

`--subagent-prompt-file PATH` optionally replaces the default delegated-task
instructions. It requires `--subagents`, reads UTF-8 text from a workspace
file, and leaves the harness-owned artifact, ordering, and completion
requirements intact. Operators can therefore probe additional child behaviors
without editing Ralph or accidentally removing the evidence contract.

The option is shared by the common smoke command and plumbing rather than
implemented independently by Claude, AGY, Nanocoder, or Cursor wrappers. A
transport that cannot dispatch a subagent reports a failed smoke check instead
of silently falling back to the basic scenario.

## Observable contract

The subagent scenario succeeds only when the parsed activity stream shows:

1. A native subagent tool dispatch.
2. A corresponding subagent tool result.
3. Meaningful tool or model activity after that result.
4. The smoke run's existing artifact and explicit-completion requirements.

The report exposes the subagent scenario as a separate check and identifies
the first missing signal. Detection uses parsed tool metadata and event order,
not prompt text or an agent-authored claim.

## Implementation boundaries

- `ralph/pipeline/plumbing/smoke_plumbing.py` owns the shared scenario,
  prompt construction, transcript analysis, result fields, and failure rules.
- `ralph/cli/commands/smoke.py` forwards the option and renders the result.
- `ralph/cli/main.py` exposes the option on each interactive smoke command.
- Tests use deterministic transcript fixtures; live token-consuming smoke runs
  remain manual and outside `make verify`.
- CLI and agent-testing documentation describe the option as a diagnostic tool,
  its pass criteria, and its token cost.

## Error handling

Missing dispatch, missing result, and missing post-result activity are distinct
errors. Existing parser, artifact, completion, timeout, and crash diagnostics
remain authoritative and can appear alongside the subagent error.
An unreadable or empty custom prompt file fails before agent invocation with an
actionable CLI error.

## Verification

TDD coverage proves option forwarding, prompt selection, ordered transcript
detection, precise failures, reporting, and unchanged default behavior. Final
verification is `make verify`, followed by a live Claude smoke run with
`--subagents` for the requested diagnosis.
