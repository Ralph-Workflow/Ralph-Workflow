# Developer plan reconciliation

> This document specifies the reconciliation model between the request, the
> plan, and repository evidence: the request and its acceptance criteria stay
> authoritative, the plan is the default execution route, and development
> analysis judges the delivered behavior independently.
>
> The `development_result` and `development_analysis_decision` artifacts are
> **free-form**: the validator mechanically checks only the frontmatter `status`
> enum, the body is the next agent's reading matter, and parallel work is the
> default. See
> `ralph/mcp/artifacts/format_docs/development_result.md` and
> `ralph/mcp/artifacts/format_docs/development_analysis_decision.md`.

## Goal

The development agent must finish small and large plans without treating an
inaccurate plan as immutable truth. The unchanged request and its acceptance
criteria remain authoritative. The plan is the default execution route and a
set of stable reporting obligations, but repository evidence may justify a
different route or show that a plan item does not apply.

Success means that the agent keeps making bounded, verified progress; every
plan item receives an auditable outcome; blocked work is reported honestly;
and the development analyzer judges the delivered behavior independently from
whether the implementation followed the plan literally.

## Authority and reconciliation model

The developer follows the plan unless fresh workspace evidence shows that a
step's route or premise is inaccurate. It must not rewrite the request,
acceptance criteria, or intended outcome to fit the implementation.

The current contract is free-form: see
`ralph/mcp/artifacts/format_docs/development_result.md` and
`ralph/mcp/artifacts/format_docs/development_analysis_decision.md`.

## Execution behavior

The developer inventories stable plan IDs and dependencies, then chooses the
execution shape from the plan's real dependency structure:

- A compact linear plan runs directly in the main session.
- A large plan is processed as ready dependency groups. Independent units may
  run concurrently with disjoint ownership; integration and final proof remain
  in the main session.

The agent reconciles a plan item only when the item becomes ready. This avoids
an unbounded up-front plan audit. For each ready item it checks the immediate
premise, records the outcome of each ready item in the result body, implements
the smallest useful increment, runs the narrowest relevant proof, records
evidence, and moves to the next ready item. After a failed approach it changes
the hypothesis or route; it does not repeat the same attempt without new
evidence. When no necessary item can make progress, it submits a partial result
with the blocker and next step.

Continuation prompts preserve this same execution, reconciliation, verified
delivery, and run-budget contract. Prior partial narrative is handoff context,
not proof.

## Analyzer behavior

Development analysis answers whether the implementation satisfies every unchanged request
and plan acceptance criterion using fresh evidence.

The analyzer independently evaluates the request, plan, and current workspace.
It must not use the implementer's summary, reason, proof, or completion claim
as evidence. A literal departure from an inaccurate implementation route is not
itself a defect. Uncovered necessary work requires changes.

The analyzer selects its outcome from fresh evidence rather than the
developer's label. It returns `completed` when the request criteria are met and
no necessary work remains, including after a partial or failed developer
handoff. It returns `request_changes` only for localized work another developer
iteration can perform. It returns `failed` when the current plan is impossible,
contradictory, not evaluable, or has no actionable developer route. A `failed`
verdict records that no safe actionable continuation exists under the current
evidence and routes back to the `development` phase; it is not a terminal phase
outcome and does not end the run.

## Cycle-terminal invariant

`terminal` always describes the boundary of the current plan/build/commit
cycle, never an irreversible judgment about the whole run. Every terminal
cycle outcome follows the same lifecycle:

1. preserve and commit useful completed, partial, or failed work;
2. record whether the cycle completed or failed for diagnostics;
3. start a fresh planning cycle when global cycle budget remains; and
4. end the run only when global cycle budget is exhausted or the operator
   explicitly cancels it.

Development-analysis `failed` routes back to the `development` phase, the same
target as `request_changes`, so both stay inside the current cycle and reach
no commit boundary. Only `completed` leaves the cycle, through
`development_final_commit_review` and `development_final_commit`.
Runtime faults that exhaust their bounded technical recovery also close and
commit the current cycle before the budget router decides whether another
planning cycle can start. Policy names such as `failed_terminal`, terminal
roles, terminal outcome rendering, and prompt prose must use this cycle-scoped
meaning consistently.

## Scope

This document covers the reconciliation model, the execution shape, the analyzer
contract, and cycle-terminal semantics. Artifact-level obligations live in
`ralph/mcp/artifacts/format_docs/development_result.md` and
`ralph/mcp/artifacts/format_docs/development_analysis_decision.md`.

## Verification design

Black-box tests cover:

- valid status with free-form markdown body parses through the public artifact seam;
- initial, continuation, worker, and fallback prompts share the execution
  loop and do not duplicate divergent rules;
- the analyzer independently audits the implementation against request criteria;
- a `completed` analyzer outcome closes and commits the current cycle, then routes to
  fresh planning whenever global cycle budget remains;
- exhausted global budget and explicit cancellation are the only run-ending
  conditions;
- compact and large-plan guidance selects progress without requiring
  unnecessary delegation; and
- existing visual proof and analysis-feedback contracts remain intact.

Focused artifact and prompt tests run before the authoritative `make verify`
gate. No new dependency, phase, command, or persistent runtime state is added.

## Prompt behaviors

These are the developer and analyzer prompt behaviors this design specifies:

- Follow the supplied plan and avoid broad rediscovery.
- Process the next ready dependency and verify each increment.
- Adapt only when fresh tool evidence falsifies the immediate premise.
- Record a bounded failure, cause, and changed next action.
- Keep shared guidance short and adjacent to the active plan.
- Use independent work units concurrently only when coordination is worthwhile.
- Require focused tool evidence and a final repository gate.
- Have analysis independently re-derive criteria.
- Approve partial/failed handoffs when fresh evidence shows no necessary work
  remains.
- Retry only actionable localized gaps inside one cycle; a `failed` verdict
  routes back to development and re-audits against the request.
