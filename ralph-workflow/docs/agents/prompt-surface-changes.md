# Prompt surface change record

This record covers the prompt-surface work that strengthens development
sessions against large or underestimated scope. It is intentionally
separated from historical measurements and operational notes; see
`docs/agents/verification.md` and the `make verify` receipts for
runtime evidence.

The current contract that this record reflects is locked by the
regression file `tests/test_prompts_no_exemption_for_failures.py` (the
S-1/S-2 ordering, recovery, and worker-isolation matrix) and
`tests/test_developer_prompt_subagent_guidance.py` (the parallel
execution wording). These tests assert the delivered prompt content
that the templates emit, not a measurement of how any live agent
performs against the prompt. No live-agent success-rate claim is made
in this record; that would require a separately authorised evaluation
harness.

## Current contract for persistence and parallel execution

The development prompt surfaces (initial, continuation, direct
fallback, worker, worker continuation, render-failure fallback) carry
a single shared recovery mandate that is rendered **before** the
request/plan payload and that occurs exactly once per surface. The
mandate names the default outcome (the entire plan / assigned unit),
the large-scope response (continue with a falsifiable increment; never
treat magnitude as a blocker), and the scope-size recovery procedure
that branches on `IS_WORKER`. The worker fallback keeps the
no-recursive-dispatch, assigned-scope, focused-`Verify`, and
worker-local artifact contracts. The main fallback carries the
scope-size recovery procedure. The render-failure fallback must still
produce a usable prompt that retains the persistence contract.

The worker prompt surfaces (dedicated `worker_developer.jinja` and
`developer_iteration_fallback.jinja` with `IS_WORKER=1`) no longer
carry the loose "If the assignment is blocked, report…" or "If the
unit remains blocked, submit…" sentence. Both have been replaced with
a reference to the canonical external-blocker rule in
`shared/_no_exemption_for_failures.j2` and an explicit loop-until-
verified contract; a free `status: partial` for any focused
difficulty is no longer an exit.

When independent work is available, the main session is required to
dispatch sub-agents within its exposed capacity and to keep its
own owned work in flight when the dispatch slots are saturated. When
the work is genuinely coupled (shared writer, integration contract)
or the runtime exposes no native sub-agent capability, the recovery
path is bounded sequential increments on the critical path with a
recomputed readiness check after every verified slice; the prompt
text makes that fallback explicit so an agent does not treat the
inability to fan out as permission to abandon the plan.

## Effect and evidence

| Change | Surface | Locked by |
| --- | --- | --- |
| Recovery mandate moved before the request/plan payload on every shipped developer surface, included exactly once. | `developer_iteration.jinja`, `developer_iteration_continuation.jinja`, `developer_iteration_fallback.jinja`, `worker_developer.jinja` | `test_s1_recovery_mandate_precedes_payload_markers`, `test_s1_recovery_mandate_occurs_exactly_once` in `tests/test_prompts_no_exemption_for_failures.py` |
| Large-scope section leads with a five-step initial action sequence that applies before any implementation and to both main and worker branches. | `shared/_developer_iteration_guidance.j2` | `test_large_scope_guidance_splits_main_and_worker_roles` (updated for the new structure) in `tests/test_prompts_no_exemption_for_failures.py` |
| Worker loose "If the assignment is blocked, report" / "If the unit remains blocked, submit" sentences replaced with a reference to the canonical external-blocker rule. | `worker_developer.jinja`, `developer_iteration_fallback.jinja` | `test_s1_worker_fallback_replaces_loose_blocked_sentence_with_canonical_rule`, `test_s1_worker_developer_replaces_loose_blocked_sentence_with_canonical_rule` in `tests/test_prompts_no_exemption_for_failures.py` |
| Worker fallback retains no-dispatch, assigned scope, focused `Verify`, and worker-local artifact path. | `developer_iteration_fallback.jinja` (with `IS_WORKER=1`) | `test_s1_worker_fallback_retains_no_dispatch_scope_focus_and_local_paths` in `tests/test_prompts_no_exemption_for_failures.py` |
| Main fallback carries the scope-size recovery procedure; worker-only directives stay scoped to the worker surface. | `developer_iteration_fallback.jinja` (main) | `test_s1_main_fallback_carries_scope_size_recovery_procedure` in `tests/test_prompts_no_exemption_for_failures.py` |
| Render-failure fallback (broken primary template, static fallback reached) still carries the persistence contract. | developer renderer + static `developer_iteration_fallback.jinja` | `test_s1_render_failure_fallback_still_carries_persistence_contract` in `tests/test_prompts_no_exemption_for_failures.py` |
| Duplicated ownership prose consolidated; "Independent ready group" defers to the single "Ownership, paths, and waves" section. | `shared/_parallel_execution.jinja` | `tests/test_developer_prompt_subagent_guidance.py` (existing source-text assertions) |

## Sampling and boundaries

The matrix above is the contract; the regression tests are the proof.
Future drift in the shared guidance partial, the parallel execution
partial, or any one of the four developer templates will fail at
least one of the listed tests.

Worker isolation remains canonical: workers never dispatch sub-agents,
never run the full repository-wide gate, and never broaden scope into
another unit's namespace. The dedicated worker partial
`shared/_worker_verification.jinja` continues to own those rules; the
fallback surfaces reuse the same contract through the shared guidance
include.

No check was removed or weakened. The full gate's proof inventory
remains unchanged, and the combined test budget of 60 seconds
(`_TOTAL_TEST_BUDGET_SECONDS` in `ralph/verify.py`) is immutable.

## Historical measurements (separated from the contract)

Earlier recorded `make verify` runtime and per-step elapsed times lived
in this record; they are not part of the current contract. The
following are kept only for traceability and are explicitly **not**
part of the prompt contract:

- A 37.90-second `make verify` elapsed figure recorded under a prior
  iteration. The current contract is the 60-second combined budget
  cap, not any specific elapsed figure; rerun `make verify` on the
  current tree to obtain a fresh measurement.
- Earlier wording that tied a partial outcome to "when the run budget
  is spent" or a stop point to "one proven increment". The current
  contract forbids both: the run budget is an input, and stopping
  after a single increment is a defect.

## Verification

The `make verify` gate exercises the documented render-integrity,
artifact-example, prompt-single-sourcing, lint, type-check, and
remaining mandatory audits on the current tree. The regression file
`tests/test_prompts_no_exemption_for_failures.py` is the unit-level
proof of the prompt contract; `make verify` is the repository-level
proof that the contract is intact.
