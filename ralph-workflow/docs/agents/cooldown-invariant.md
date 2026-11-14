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

Every cooldown decision is logged once, by the owner, with the phase, the
agent, the `UnavailabilityReason`, the duration and the expiry
(`cooldown_decision` in `ralph/recovery/controller.py`).

## Audited agent-invoking call sites

| Call site | Disposition | Proof |
| --- | --- | --- |
| Main pipeline phases (`ralph/pipeline/effect_executor.py`, `ralph/pipeline/runner.py`) | uses owner | `ralph/pipeline/reducer.py:153,211,293` route the attempt outcome through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Reducer and session runtime (`ralph/pipeline/reducer.py`, `ralph/_session_runtime_runtime.py`) | uses owner | `recovery.handle` is the only failure path; `ralph/pipeline/runner.py:846` |
| Parallel phase workers (`ralph/pipeline/parallel/worker_runtime.py`) | uses owner | the worker returns the attempt outcome to the phase handler, which the reducer routes through `recovery.handle`; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Conflict resolution (`ralph/pipeline/conflict_resolution/`) | uses owner | `ralph/pipeline/conflict_resolution/session.py` classifies through `RecoveryController.classify_conflict_attempt`; `tests/test_conflict_stalled_attempt_cooldown.py`, `tests/recovery/test_conflict_progress_classification.py` |
| Rebase and auto-integrate recovery (`ralph/pipeline/_pending_merge_repair.py`, `ralph/pipeline/_integration_diagnostic.py`, `ralph/pipeline/_integration_continuation.py`) | uses owner | resolution failures resolve through `ralph/pipeline/resolution_outcome_producer.py` into the owner's classification; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Smoke-test plumbing (`ralph/pipeline/plumbing/smoke_plumbing.py`) | uses owner | the attempt outcome reaches the ledger through the shared owner; `tests/recovery/test_cooldown_parity_all_phases.py` |
| Commit plumbing and the `ralph commit` CLI (`ralph/pipeline/plumbing/commit_plumbing.py`, `ralph/cli/commands/commit.py`) | uses owner, fixed: the chain builds one `RecoveryController` and reuses it across outer retries, so a benched agent keeps its cooldown state | `tests/test_commit_retry_cooldown.py` |
| Project-policy flows (`ralph/project_policy/analysis.py`, `remediation.py`, `pipeline_driver.py`, `cli_integration.py`) | uses owner, fixed: a caught launch failure is classified and handled by the owner, a cooled-down candidate is skipped, and an all-unavailable chain consults `earliest_available_wait_ms` | `tests/project_policy/test_policy_invocation_cooldown.py` |

## Documented exemptions

Two paths cool down something other than an agent, so they are outside
the agent owner by construction. They are exemptions, not gaps.

| Path | Exemption | Proof |
| --- | --- | --- |
| Canary and dead-agent probes (`ralph/recovery/canary_probe.py`) | a probe measures one agent's health on purpose; it runs in a non-charging mode that neither charges the ledger nor benches the agent | `tests/recovery/test_dead_agent_pipeline_matrix.py`, `tests/recovery/test_canary_probe.py` |
| MCP server restart and explore recovery (`ralph/mcp/server/_mcp_restart_policy.py`, `ralph/mcp/explore/recovery.py`) | these cool down MCP servers and tools, not agents, and carry their own backoff for a different resource | `tests/test_mcp_restart_policy_default_cooldown.py` |

## How the contract is enforced

- **Structural audit.** `ralph/testing/audit_agent_failure_single_owner.py`
  runs in `make verify`. It fails when a production agent-invoking call
  site catches a failure without routing it through `classify_*` and
  `handle`, when a `ClassifiedFailure` disables both the budget charge
  and unavailability outside an allowlisted progress path, and when a
  phase module defines its own cooldown, skip-list or backoff state.
- **Cross-phase contract test.**
  `tests/recovery/test_cooldown_parity_all_phases.py` is parametrised
  over every agent-invoking phase of the real default policy, loaded
  through `ralph.policy.loader.cached_default_pipeline_policy`. For each
  phase it drives a stub agent that fails the same way N times and
  asserts the identical owner behaviour: `Cooldown` after the owner's
  threshold, the next candidate selected, the wait equal to
  `earliest_available_wait_ms`, and the reason and duration taken from
  the `UnavailabilityReason` policy table. The matrix covers a
  "stalled but reported success" agent and a "no activity" agent, and
  includes the conflict-resolution sub-pipeline.
- **Behavioural tests.** Each audited path above is pinned by the test
  named in its row.

## Where to go next

- [Recovery](../sphinx/recovery.md) — the operator-facing view of retries,
  fallover and resume.
- [Agent invoke architecture](architecture.md) — how an attempt reaches
  the owner.
