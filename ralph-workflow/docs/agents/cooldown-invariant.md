# Agent cooldown: the sole cooldown system

> **Role:** This page is the one and only definition of agent availability
> in the Ralph Workflow Python package. It defines the cooldown system, its
> rules and durations, names the one component that owns cooldown, budget
> charge and backoff, lists the audited agent-invoking call sites, and names
> the checks that keep the contract true. No other document defines agent
> cooldown, availability, demotion or benching rules; other pages summarise
> this one and link here.

## Terminology: every name means the cooldown system

An agent has exactly two states: **on cooldown**, with a finite expiry, or
**available**. There is no other agent-availability system, no third state
and no second set of rules. Every word in the table below, wherever it
appears (code, logs, operator output, older changelog entries, issues or
conversation), refers to this same cooldown system and means one thing:
**the agent is on cooldown, with a finite expiry, and is retried when the
cooldown expires.**

| Alias | Meaning |
| --- | --- |
| bench, benched, benching | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires |
| demote, demoted, demotion | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires |
| spent | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires |
| exhausted | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires; an "exhausted chain" is a chain whose every agent is on cooldown, and the phase waits for the earliest expiry |
| unavailable | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires |
| excluded | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires (`excluded_from_this_selection` only skips an agent for one selection, for example a resolver already invoked in the current round) |
| skipped | the agent is on cooldown, with a finite expiry, and is retried when the cooldown expires |
| dead-agent bench | a `dead_agent_no_progress` cooldown, with a finite expiry; the agent is retried when it expires |

These words are not names for anything else about an agent. The engine and
the docs use only the cooldown terms ("on cooldown", "cooling down",
"available", "eligible"); `ralph/testing/audit_cooldown_only.py` rejects the
old vocabulary for agent availability. The same words keep unrelated meanings
that are not agent availability: a spent or exhausted budget (CPU, conflict,
analysis or probe budget), an exhausted provider quota or context window
(which starts the `out_of_credits` or `context_exhausted` cooldown), display
colour demotion, and the explore `bench` benchmark harness.

## The invariant

An agent is either on cooldown or available. There are no other states.
Every exclusion of an agent from selection is a cooldown with a finite
expiry and a reason; when the expiry passes, the agent is selectable again
and is retried. Unproductive limits (`unproductive_limit`), commit retry
limits (`retry_limit`), dead-agent verdicts and failed canary probes
(`dead_agent_no_progress`), and dead conflict-resolution tool surfaces
(`candidate_unavailable`) each start such a cooldown. When every agent in a
chain is cooling down, including a sole agent, the phase waits until the
earliest expiry; it never fails for that.

There is exactly one cooldown system, owned by
`RecoveryController` together with `AgentUnavailabilityTracker` and
`AgentFailureLedger` (`ralph/recovery/`). Every place that invokes an
agent, in every phase and sub-pipeline:

1. reports each failed or unproductive attempt to that owner through the
   same classification and handling path (`classify_*` then `handle`);
2. lets the owner decide cooldown, budget charge and backoff, using the
   same `UnavailabilityReason` policy table
   (`ralph/recovery/unavailability_reason.py`), the same exponential
   backoff, and the same two results, `Cooldown(until_ms, reason)` and
   `Eligible`;
3. selects the next agent only through `preferred_agent_index` and
   `chain_eligibility`;
4. waits on, or terminates on, cooldown expiry through the same
   `earliest_available_wait_ms` and `earliest_eligible_wait_ms`
   behaviour.

No phase keeps its own retry counter, its own skip list, or its own
sleep-and-retry loop. A failure class the owner does not penalise is a
defect unless it is provably progress: the attempt changed the
workspace, produced a valid artifact, or reduced the work remaining.

## The owner

| Component | Module | Responsibility |
| --- | --- | --- |
| `RecoveryController` | `ralph/recovery/controller.py` | classification, handling, eligibility, backoff |
| `AgentUnavailabilityTracker` | `ralph/recovery/agent_unavailability_tracker.py` | per-agent cooldown windows and expiry |
| `AgentFailureLedger` | `ralph/recovery/agent_failure_ledger.py` | unproductive-attempt counts and the `unproductive_limit` cooldown |

Cooldown reasons and their durations come from `UnavailabilityReason` and
`DEFAULT_UNAVAILABILITY_BACKOFF_POLICY` in
`ralph/recovery/unavailability_reason.py`. There is no second policy
table.

Every cooldown is logged once, by the owner, with the phase, the
agent, the `UnavailabilityReason`, the duration and the expiry
(`cooldown_decision` in `ralph/recovery/controller.py`).

## Cooldown rules

These are the only agent cooldown rules. Other pages summarise them and
link here.

### Reasons and durations

Every cooldown carries one `UnavailabilityReason`. Its first duration is
the reason's base delay; each repeat of a failure for the same agent
doubles it, up to the reason's cap.

| Reason | Started by | Base | Cap |
| --- | --- | --- | --- |
| `out_of_credits`, `provider_unavailable` | rate or quota limits, provider overloads, `QuotaExhaustedError` | 60 s | 5 h |
| `context_exhausted`, `process_exited`, `auth_config`, `broken_agent`, `no_output_at_start` | a full context window, an unexplained non-zero exit, credential or configuration failures, `BrokenAgentExitError`, no output at start | 5 s | 5 h |
| `no_output_after_activity`, `suspicious_timeout_no_output`, `dead_agent_no_progress` | silence after activity, a suspicious timeout, a confirmed dead-agent verdict | 10 s | 5 h |
| `stale_child_quiet`, `strictly_stuck` | a quiet stale child process, a strictly stuck session | 15 s | 5 h |
| `unproductive_limit`, `retry_limit`, `candidate_unavailable` | the ledger's unproductive limits, commit retry limits, a dead conflict-resolution tool surface or unregistered commit agent | 60 s | 1 h |

Pi's structured retry and context-length events keep their specific reason
even when the Pi process exits non-zero. The activity transcript emits a
`RECOVERING` line with the reason, the cooldown and the fallback choice.

### Growth, caps and reset

- Each `AgentUnavailabilityTracker.mark_unavailable` call doubles the
  reason's base delay using the agent's prior attempt counter. History is
  keyed by agent alone and survives cooldown expiry and phase changes.
- The delay is clamped at the smaller of the reason's cap and the universal
  5-hour ceiling (`_UNIVERSAL_COOLDOWN_CAP_MS = 18_000_000` ms); the attempt
  counter saturates with it.
- A custom `RecoveryControllerOptions.unavailability_backoff_policy` may set
  smaller caps; caps above the universal ceiling are clamped to it.
- A later, shorter cooldown never shortens a longer one the agent is
  already on.
- A successful attempt that produces a valid artifact clears only that
  agent's cooldown and backoff history (`RecoveryController.reset_backoff`);
  its next failure starts at the reason's base delay. When a cooldown
  expires, the agent's next invocation is the probe: success resets its
  history and a repeat failure starts the next, longer cooldown.
- Cooldowns and backoff history survive a restart. After every reduce the
  pipeline checkpoint stores each active window as a wall-clock expiry,
  with its reason and the agent's attempt counter, in
  `PipelineState.agent_cooldowns`. On `--resume`,
  `build_entry_point_recovery_controller` rebases each expiry onto the new
  process's monotonic clock, so the time the engine was down counts toward
  the window. Windows that expired while the engine was down are dropped,
  and a restored window is clamped at the universal 5-hour ceiling. The
  attempt counter is restored as it was, so the next cooldown keeps
  doubling. A checkpoint written before this field existed resumes with no
  cooldowns.

### Unproductive limits (`AgentFailureLedger`)

An attempt is unproductive when it ends without a valid artifact.

- `[general] agent_max_consecutive_unproductive` (default `3`): that many
  consecutive unproductive attempts in one phase start an
  `unproductive_limit` cooldown. A workspace change resets only this count.
  Only a write counts as a workspace change: the workspace watcher reports
  file opens and read-only closes as agent activity, never as a change, so
  an agent that only reads files keeps counting toward the limit.
- Whatever the failure type, the same agent cannot fail more than three
  attempts in a row in one phase without a cooldown when any of those
  failures was not charged to it (for example identical failures across
  every peer, an engine-side configuration fault or a runtime-ended
  attempt). `RecoveryController.finalize_attempt` counts failed attempts per
  phase and agent, resets the count on a successful attempt, on an attempt
  that wrote to the workspace, or whenever a cooldown is logged for the
  agent, and starts an `unproductive_limit` cooldown on the third
  (`MAX_FAILURES_WITHOUT_COOLDOWN`). A streak of charged failures stays
  governed by `agent_max_consecutive_unproductive`.
- `[general] agent_max_total_unproductive` (default `9`): that many
  unproductive attempts in total, with or without a workspace change, start
  the same `unproductive_limit` cooldown.
- The `unproductive_limit` cooldown starts at 60 seconds, doubles for each
  earlier `unproductive_limit` cooldown since the agent's last valid
  artifact, and is capped at 3600 seconds; the durations are not
  configurable. Starting it resets both counts. A valid artifact clears the
  agent's ledger history. Counts survive restarts in persisted state.

### Dead-agent and broken-agent cooldowns

- A confirmed dead-agent verdict (see
  [Broken-agent detection](broken-agent-detection.md) for how it is judged)
  starts a `dead_agent_no_progress` cooldown in the same tracker: 10 seconds
  first, doubling on each repeat, capped at 5 hours. An `unknown` verdict or
  an inconclusive canary probe never starts one.
- A `BrokenAgentExitError` starts a `broken_agent` cooldown and skips
  same-agent retries, so the chain falls over at once when it has another
  agent.
- Typed provider signals (`QuotaExhaustedError`, `MissingCredentialsError`,
  `BrokenAgentExitError` prompt echoes) start their cooldown on the first
  failure without a canary probe.
- False-cooldown measurement: each dead-agent cooldown opens a candidate
  keyed by the agent configuration fingerprint (agent name, binary, model,
  model flag, parser, transport). A later success under the same
  fingerprint closes it and counts one false cooldown
  (`FalseCooldownFeedback`); the run-time report prints the count as
  `False cooldowns`.

### When every agent is on cooldown

- Selection skips agents on cooldown. When every agent in a chain is on
  cooldown, including a sole configured agent, the phase waits until the
  earliest expiry and then retries; it never fails for that.
- Conflict resolution follows the same rule. A round whose candidates are
  all cooling down with a known expiry ends `CANDIDATE_UNAVAILABLE` and waits
  for the expiry without charging the conflict budget. A round that has no
  runnable candidate left ends `NO_RUNNABLE_CANDIDATE`; its message names
  every resolver agent with its cooldown reason and expiry ("all N resolver
  agents are on cooldown (...)"), or says "no resolver agents are
  configured" when the candidate list is empty. Integration stays pending
  with the rebase preserved and resumes when a resolver agent is eligible
  again.
- Every cooldown is logged once, when it starts, as a `cooldown_decision`
  line with the phase, agent, reason, duration and expiry.

## Audited agent-invoking call sites

Each path is marked **already compliant** or **fixed**. Optional pipeline
phases such as `fix`, `review`, and `design_verdict` use the same main
pipeline owner when declared; the default bundled pipeline's non-terminal
phases are planning, planning analysis, development, development analysis,
commit review (per-iteration and final), and commit (per-iteration and final).

| Call site | Disposition | Proof |
| --- | --- | --- |
| Main pipeline phases (`ralph/pipeline/effect_executor.py`, `ralph/pipeline/runner.py`) | already compliant | the registered production runner receives a `RecoveryController`, and the reducer routes classified outcomes through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py`, `tests/pipeline/test_reducer_single_owner.py` |
| Reducer and session runtime (`ralph/pipeline/reducer.py`, `ralph/_session_runtime_runtime.py`) | already compliant | failed attempts reach the shared recovery handler; `tests/pipeline/test_reducer_single_owner.py` |
| Parallel phase workers (`ralph/pipeline/parallel/worker_runtime.py`) | already compliant | the worker returns the attempt outcome to the phase handler, which the reducer routes through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Conflict resolution (`ralph/pipeline/conflict_resolution/`) | fixed | `ralph/pipeline/conflict_resolution/session.py` passes typed outcomes to the shared owner's `FailureClassifier.classify_outcome` path, and `RecoveryController.handle` applies the cooldown, budget, and selection policy; `tests/test_conflict_resolution_phase_parity.py`, `tests/test_conflict_resolution_phase_parity_cooldown.py`, `tests/test_conflict_stalled_attempt_cooldown.py`, `tests/recovery/test_conflict_progress_classification.py` |
| Rebase and auto-integrate recovery (`ralph/pipeline/_pending_merge_repair.py`, `ralph/pipeline/_integration_diagnostic.py`, `ralph/pipeline/_integration_continuation.py`) | fixed | resolution failures resolve through `ralph/pipeline/resolution_outcome_producer.py` into the owner's classification; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Smoke-test plumbing (`ralph/pipeline/plumbing/smoke_plumbing.py`) | already compliant | the attempt outcome reaches the ledger through the shared owner; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Commit plumbing and the `ralph commit` CLI (`ralph/pipeline/plumbing/commit_plumbing.py`, `ralph/cli/commands/commit.py`) | already compliant | the chain retains one `RecoveryController` across outer retries, so cooldown state survives; `tests/test_commit_retry_cooldown.py` |
| Project-policy flows (`ralph/project_policy/analysis.py`, `remediation.py`, `pipeline_driver.py`, `cli_integration.py`) | fixed | every attempt runs through `run_chain_attempt` in `ralph/pipeline/attempt_runner.py`, which selects through the owner, waits through `earliest_available_wait_ms`, and finalizes a remediation attempt that resolved no finding as unproductive through its `progress_check`; `tests/project_policy/test_policy_invocation_cooldown.py`, `tests/project_policy/test_policy_remediation_no_progress_cooldown.py` |
| Canary and dead-agent probes (`ralph/recovery/canary_probe.py`) | fixed | the canary runner is registered through `AgentEntryPoint.CANARY_PROBE` and invokes `run_agent_attempt` with a recovery controller; `tests/recovery/test_canary_probe.py`, `tests/recovery/test_dead_agent_pipeline_matrix.py` |

## Documented exemptions

Three paths cool down something other than an agent, so they are outside
the agent owner by construction. They are exemptions, not gaps.

| Path | Exemption | Proof |
| --- | --- | --- |
| MCP server restart policy (`ralph/mcp/server/_mcp_restart_policy.py`) | cools down MCP server restart attempts, not agents | `tests/test_mcp_restart_policy_default_cooldown.py` |
| MCP explore recovery (`ralph/mcp/explore/recovery.py`) | cools down explore-tool recovery actions, not agents | `tests/test_explore_recovery.py` |
| Auto-integrate retry backoff (`ralph/pipeline/auto_integrate_backoff.py`, catch-up backoff at `ralph/pipeline/auto_integrate_catchup.py:381`) | cools down non-agent git-operation retry attempts, not agents | `tests/test_auto_integrate_backoff_clock.py`, `tests/test_auto_integrate_catchup.py` |

## How the contract is enforced

- **Two-state audit.** `ralph/testing/audit_cooldown_only.py` runs in
  `make verify` as the step "cooldown-only agent availability audit
  (audit_cooldown_only)". It fails when the ledger declares an agent
  status other than `Eligible` and an expiring `Cooldown`, when
  `WaitKind` gains a member beyond `AVAILABLE_NOW`, `COOLING_DOWN` and
  `NO_CANDIDATES`, when a module outside the owner calls
  `mark_unavailable`, or when an exclusion-state identifier appears under
  `ralph/recovery/` or `ralph/pipeline/`. It also bans the old agent
  vocabulary (`bench`, `spent`, `demot`, `exhaust` word forms) in
  identifiers and string literals under `ralph/recovery/`,
  `ralph/pipeline/conflict_resolution/` and the agent-selection and
  resolver-candidate modules of `ralph/pipeline/`, with a short, reasoned
  allowlist for unrelated meanings (budgets, provider quota and context
  exhaustion, persisted conflict-ladder fields, legacy checkpoint keys).
  The same ban covers test module filenames and `test_*` function names
  under `tests/`, with a reasoned allowlist for unrelated meanings (the
  explore benchmark harness, display colour demotion, parser iterator
  exhaustion, cycle, gate and integration-ladder budgets, legacy-migration
  tests that must name the legacy field).
  It scans `docs/**/*.md`, `AGENTS.md` and `CONTRIBUTING.md` for
  agent-availability bench, demotion or spent phrasing, requires this page
  and its Terminology section with every alias, requires every other
  `docs/` section whose heading names cooldown, demotion, bench, backoff or
  agent availability to link here, requires `AGENTS.md` to name this page,
  and requires `AgentUnavailabilityTracker` and `AgentFailureLedger` to
  declare `COOLDOWN_SYSTEM_DOC` pointing here. It also requires its guard
  tests to exist and not be skipped or xfailed:
  `tests/recovery/test_cooldown_only_invariant.py`,
  `tests/recovery/test_unproductive_limit_cooldown_regression.py`, and
  `tests/test_audit_cooldown_only.py`.
- **Shared attempt path audit.** `ralph/testing/audit_agent_attempt_path.py`
  runs in `make verify` as the step "agent attempt path audit
  (audit_agent_attempt_path)". It fails when a module outside a short,
  reasoned allowlist calls `preferred_agent_index`,
  `earliest_available_wait_ms` or `earliest_eligible_wait_ms`, when a module
  outside the registered invoke seams references
  `ralph.agents.invoke.invoke_agent`, and when an `InvokeAgentEffect` that
  may complete without an artifact (`requires_completion_evidence` not
  `True`) is built in a module that passes no `progress_check` to the shared
  attempt path. New sub-pipelines call `run_chain_attempt`.
- **Structural audit.** `ralph/testing/audit_agent_failure_single_owner.py`
  runs in `make verify`. Its ownership rules in
  `ralph/testing/_single_owner_rules.py` include:
  - `production_agent_failure_owner_bypass`: a caught agent failure does
    not route through `classify_*` followed by `handle`;
  - `production_agent_invoker_unchecked`: an agent invocation is neither
    guarded nor given an owner and is not a registered runner invoke seam;
  - `production_agent_runner_no_controller`: `run_agent_attempt` is
    invoked without an owner;
  - `production_agent_runner_controller_only`: the runner owner is null,
    nullable, or an unnarrowed optional value;
  - `production_agent_failure_recovery_unreachable`: the recovery call is
    in a statically unreachable branch or follows a terminating statement;
  - `production_unjustified_non_penalising_failure`: a
    `ClassifiedFailure` disables both budget charge and unavailability
    outside the registered progress path; and
  - `production_phase_failure_state` and `production_streak_counter_state`:
    production code defines phase-local cooldown, skip-list, backoff, or
    competing failure-counter state.
- **Cross-phase contract test.**
  `tests/recovery/test_cooldown_parity_all_phases.py` loads the production
  default policy through
  `ralph.policy.loader.cached_default_policy_bundle` and drives every
  non-terminal, non-`skip_invocation` phase from that pipeline plus the
  `policy_remediation` and `policy_remediation_analysis` routes. For the
  bundled default that set is `planning`, `planning_analysis`,
  `development`, `development_commit_review`, `development_commit`,
  `development_analysis`, `development_final_commit_review`,
  `development_final_commit`, `policy_remediation`, and
  `policy_remediation_analysis`. Each route's failed attempt is routed
  through the owner's own classification and handling API
  (`RecoveryController.classify_*` followed by `handle`), and the suite
  asserts `Cooldown` after the owner threshold, next-candidate selection,
  and the owner's `earliest_available_wait_ms`. The matrix covers launch
  failures (`PROCESS_EXITED`) and no-activity failures
  (`NO_OUTPUT_AT_START`) with reasons and durations from the
  `UnavailabilityReason` policy table, as well as stalled agents reporting
  success without valid artifacts cooling down for
  `unproductive_limit`. Conflict resolution is exercised
  separately via paused-rebase integration scenarios. Its mutation check
  proves that removing the classification-to-handler handoff makes the contract
  test fail. The production owner handoff of the main loop is pinned separately
  by `tests/pipeline/test_reducer_single_owner.py`, and the conflict
  resolution driver's own classification is pinned by
  `tests/test_conflict_resolution_phase_parity.py`.
- **Owner-lifetime contract tests.**
  `tests/test_commit_retry_cooldown.py` drives the commit outer-retry path
  and requires every outer attempt to share one owner instance, preserving
  cooldown state. `tests/project_policy/test_policy_invocation_cooldown.py`
  drives the project-policy production entry point and requires an
  all-unavailable chain to wait through `earliest_available_wait_ms` and a
  caught launch failure to reach the owner.
- **Conflict-resolution contract.** A no-progress attempt has a typed
  outcome and cools down with its typed reason, including
  `no_output_at_start`, `no_output_after_activity`, or
  `dead_agent_no_progress`; it counts against the owner budget. Selection and
  waiting remain owner decisions, and the driver does not replace a
  no-selectable-agent result with a local choice. `max_rounds_per_stop`
  bounds resolution rounds and reaching the cap escalates instead of
  invoking another resolver. This is pinned by
  `tests/test_conflict_resolution_phase_parity.py`,
  `tests/test_conflict_resolution_phase_parity_cooldown.py`,
  `tests/test_conflict_resolution_round_cap.py`,
  `tests/test_conflict_stalled_attempt_cooldown.py`,
  `tests/recovery/test_conflict_progress_classification.py`, and
  `tests/recovery/test_cooldown_parity_all_phases.py`.
- **Behavioural tests.** Each audited path above is pinned by the test
  named in its row.

## Where to go next

- [Recovery](../sphinx/recovery.md) — the operator-facing view of retries,
  fallover and resume.
- [Broken-agent detection](broken-agent-detection.md) — how an attempt is
  judged before a dead-agent or broken-agent cooldown starts.
- [Agent invoke architecture](architecture.md) — how an attempt reaches
  the owner.
