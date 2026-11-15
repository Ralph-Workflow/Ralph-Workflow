# Agent cooldown: the single-owner invariant

> **Role:** This page is the contract for agent failure handling in the
> Ralph Workflow Python package. It names the one component that owns
> cooldown, budget charge and backoff, lists the audited agent-invoking
> call sites, and names the checks that keep the contract true.

## The invariant

There is exactly one cooldown and unavailability system, owned by
`RecoveryController` together with `AgentUnavailabilityTracker` and
`AgentFailureLedger` (`ralph/recovery/`). Every place that invokes an
agent, in every phase and sub-pipeline:

1. reports each failed or unproductive attempt to that owner through the
   same classification and handling path (`classify_*` then `handle`);
2. lets the owner decide cooldown, budget charge, backoff and
   spent-marking, using the same `UnavailabilityReason` policy table
   (`ralph/recovery/unavailability_reason.py`), the same exponential
   backoff, and the same `Cooldown` / `Spent` / `Eligible` results;
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
| `AgentFailureLedger` | `ralph/recovery/agent_failure_ledger.py` | failure counts, demotion, ledger cooldown |

Cooldown reasons and their durations come from `UnavailabilityReason` and
`DEFAULT_UNAVAILABILITY_BACKOFF_POLICY` in
`ralph/recovery/unavailability_reason.py`. There is no second policy
table.

Every cooldown is logged once, by the owner, with the phase, the
agent, the `UnavailabilityReason`, the duration and the expiry
(`cooldown_decision` in `ralph/recovery/controller.py`).

## Audited agent-invoking call sites

| Call site | Disposition | Proof |
| --- | --- | --- |
| Main pipeline phases (`ralph/pipeline/effect_executor.py`, `ralph/pipeline/runner.py`) | uses owner | the registered production runner receives a `RecoveryController`, and the reducer routes classified outcomes through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py`, `tests/pipeline/test_reducer_single_owner.py` |
| Reducer and session runtime (`ralph/pipeline/reducer.py`, `ralph/_session_runtime_runtime.py`) | uses owner | failed attempts reach the shared recovery handler; `tests/pipeline/test_reducer_single_owner.py` |
| Parallel phase workers (`ralph/pipeline/parallel/worker_runtime.py`) | uses owner | the worker returns the attempt outcome to the phase handler, which the reducer routes through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Conflict resolution (`ralph/pipeline/conflict_resolution/`) | uses owner | `ralph/pipeline/conflict_resolution/session.py` classifies typed outcomes through `RecoveryController.classify_conflict_attempt`; `tests/test_conflict_resolution_phase_parity.py`, `tests/test_conflict_resolution_phase_parity_cooldown.py`, `tests/test_conflict_stalled_attempt_cooldown.py`, `tests/recovery/test_conflict_progress_classification.py` |
| Rebase and auto-integrate recovery (`ralph/pipeline/_pending_merge_repair.py`, `ralph/pipeline/_integration_diagnostic.py`, `ralph/pipeline/_integration_continuation.py`) | uses owner | resolution failures resolve through `ralph/pipeline/resolution_outcome_producer.py` into the owner's classification; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Smoke-test plumbing (`ralph/pipeline/plumbing/smoke_plumbing.py`) | uses owner | the attempt outcome reaches the ledger through the shared owner; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Commit plumbing and the `ralph commit` CLI (`ralph/pipeline/plumbing/commit_plumbing.py`, `ralph/cli/commands/commit.py`) | uses owner | the chain retains one `RecoveryController` across outer retries, so bench state survives; `tests/test_commit_retry_cooldown.py` |
| Project-policy flows (`ralph/project_policy/analysis.py`, `remediation.py`, `pipeline_driver.py`, `cli_integration.py`) | uses owner | a caught launch failure is classified and handled, cooled-down candidates are skipped, and an all-unavailable chain waits through `earliest_available_wait_ms`; `tests/project_policy/test_policy_invocation_cooldown.py` |
| Canary and dead-agent probes (`ralph/recovery/canary_probe.py`) | uses owner | the canary runner is registered through `AgentEntryPoint.CANARY_PROBE` and invokes `run_agent_attempt` with a recovery controller; `tests/recovery/test_canary_probe.py`, `tests/recovery/test_dead_agent_pipeline_matrix.py` |

## Documented exemptions

Two paths cool down something other than an agent, so they are outside
the agent owner by construction. They are exemptions, not gaps.

| Path | Exemption | Proof |
| --- | --- | --- |
| MCP server restart policy (`ralph/mcp/server/_mcp_restart_policy.py`) | cools down MCP server restart attempts, not agents | `tests/test_mcp_restart_policy_default_cooldown.py` |
| MCP explore recovery (`ralph/mcp/explore/recovery.py`) | cools down explore-tool recovery actions, not agents | `tests/test_explore_recovery.py` |

## How the contract is enforced

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
  `ralph.policy.loader.cached_default_pipeline_policy` and drives the
  registered production invocation route for `planning`,
  `planning_analysis`, `development`, `development_commit_review`,
  `development_commit`, `development_analysis`,
  `development_final_commit_review`, `development_final_commit`, and
  `rebase_conflict_resolution`. Each route's failed attempt is routed
  through the owner's own classification and handling API
  (`RecoveryController.classify_*` followed by `handle`), and the suite
  asserts `Cooldown` after the owner threshold, next-candidate
  selection, the owner's `earliest_available_wait_ms`, and the reason
  and duration from the `UnavailabilityReason` policy table. The matrix
  includes a stalled agent that reports success and a no-activity agent.
  Its mutation check proves that removing the classification-to-handler
  handoff makes the contract test fail. The production owner handoff of
  the main loop is pinned separately by
  `tests/pipeline/test_reducer_single_owner.py`, and the conflict
  resolution driver's own classification is pinned by
  `tests/test_conflict_resolution_phase_parity.py`.
- **Owner-lifetime contract tests.**
  `tests/test_commit_retry_cooldown.py` drives the commit outer-retry path
  and requires every outer attempt to share one owner instance, preserving
  bench state. `tests/project_policy/test_policy_invocation_cooldown.py`
  drives the project-policy production entry point and requires an
  all-unavailable chain to wait through `earliest_available_wait_ms` and a
  caught launch failure to reach the owner.
- **Conflict-resolution contract.** A no-progress attempt has a typed
  outcome and is benched with `dead_agent_no_progress` or
  `no_output_at_start`; it counts against the owner budget. Selection and
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
- [Agent invoke architecture](architecture.md) — how an attempt reaches
  the owner.
