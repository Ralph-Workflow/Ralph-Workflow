# Developer plan reconciliation

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

Every required plan-step or work-unit ID receives exactly one disposition in
the development result:

- `completed`: the item was necessary and followed substantially as written.
- `adapted`: the item was necessary, but evidence required a different route
  that achieves the same intended outcome.
- `not_applicable`: the item is unnecessary because its premise is false, its
  outcome is already satisfied, or another necessary item demonstrably
  supersedes it without weakening a request criterion.
- `blocked`: the item remains necessary but cannot be completed with the
  available authority, evidence, or environment.

A completed result may contain `completed`, `adapted`, and `not_applicable`
items. It may not contain `blocked`; blocked necessary work requires a partial
result. Every plan ID remains present even when it is not applicable.

## Evidence contract

Each plan proof carries a `Disposition:` field. A `completed` proof cites the
changed or observed location and the focused verification. An `adapted` proof
also states the inaccurate plan assumption and proves that the alternate route
preserves the intended outcome.

A `not_applicable` proof must identify:

1. the plan assumption that does not hold;
2. fresh evidence that contradicts the premise or proves the outcome already
   exists;
3. why omitting the step leaves every request criterion covered; and
4. a location or command from which a verifier can re-derive the evidence.

Difficulty, elapsed time, a passing unrelated gate, or an unsupported claim
that another step covers the work never justifies `not_applicable`. The
artifact validator enforces the closed disposition vocabulary and rejects a
completed result containing `blocked`. Semantic sufficiency remains the
development analyzer's job because a structural validator cannot decide
whether evidence actually supports a repository claim.

## Execution behavior

The developer inventories stable plan IDs and dependencies, then chooses the
execution shape from the plan's real dependency structure:

- A compact linear plan runs directly in the main session.
- A large plan is processed as ready dependency groups. Independent units may
  run concurrently with disjoint ownership; integration and final proof remain
  in the main session.

The agent reconciles a plan item only when the item becomes ready. This avoids
an unbounded up-front plan audit. For each ready item it checks the immediate
premise, selects a disposition, implements the smallest useful increment, runs
the narrowest relevant proof, records evidence, and moves to the next ready
item. After a failed approach it changes the hypothesis or route; it does not
repeat the same attempt without new evidence. When no necessary item can make
progress, it submits a partial result with the blocker and next step.

Continuation prompts preserve this same execution, reconciliation, verified
delivery, and run-budget contract. Prior partial narrative is handoff context,
not proof.

## Analyzer behavior

Development analysis answers two separate questions:

1. Does the implementation satisfy every unchanged request and plan acceptance
   criterion using fresh evidence?
2. Is every `adapted` or `not_applicable` plan disposition supported by
   re-derivable evidence without reducing request coverage?

The analyzer independently derives plan dispositions from the request, plan,
and current workspace. It must not use the implementer's disposition, summary,
rationale, proof, or completion claim as evidence. A literal departure from an
inaccurate implementation route is not itself a defect. An unjustified
`not_applicable`, an alternate route that misses the original outcome, or a
completed result containing uncovered necessary work requires changes.

The analyzer selects its cycle outcome from fresh evidence rather than the
developer's label. It returns `completed` when the request criteria are met and
no necessary work remains, including after a partial or failed developer
handoff. It returns `request_changes` only for localized work another developer
iteration can perform. It returns terminal `failed` when the current plan is
impossible, contradictory, not evaluable, or has no actionable developer route.
That terminal decision ends the current planning/development cycle; it does not
declare the overall objective permanently impossible and does not end the run
while global cycle budget remains.

## Cycle-terminal invariant

`terminal` always describes the boundary of the current plan/build/commit
cycle, never an irreversible judgment about the whole run. Every terminal
cycle outcome follows the same lifecycle:

1. preserve and commit useful completed, partial, or failed work;
2. record whether the cycle completed or failed for diagnostics;
3. start a fresh planning cycle when global cycle budget remains; and
4. end the run only when global cycle budget is exhausted or the operator
   explicitly cancels it.

Development-analysis `failed` therefore routes through the same cleanup and
commit boundary as `completed`, while carrying a failed cycle outcome into
post-commit routing. `request_changes` alone remains inside the current cycle.
Runtime faults that exhaust their bounded technical recovery also close and
commit the current cycle before the budget router decides whether another
planning cycle can start. Policy names such as `failed_terminal`, terminal
roles, terminal outcome rendering, and prompt prose must use this cycle-scoped
meaning consistently.

## Compatibility and scope

The markdown section name and stable plan IDs remain unchanged. The new
`Disposition:` field is required for completed development results, making old
in-flight completed drafts fail with a repairable diagnostic rather than being
silently misread. Partial results remain free-form so a constrained agent can
always report honest progress.

This change updates the development-result model, markdown mapping and format
documentation, developer and worker prompt variants, development analyzer, and
their behavioral tests. It does not let developers edit plan artifacts, change
request criteria, weaken proof coverage, or bypass final verification.

## Verification design

Black-box tests cover:

- all four dispositions parse through the public markdown artifact seam;
- unknown or missing dispositions fail completed results;
- `blocked` fails a completed result but remains available in a partial handoff;
- proof coverage still requires every canonical plan or work-unit ID;
- initial, continuation, worker, and fallback prompts share the reconciliation
  loop and do not duplicate divergent rules;
- the analyzer independently audits adapted and not-applicable items;
- terminal analyzer outcomes close and commit the current cycle, then route to
  fresh planning whenever global cycle budget remains;
- exhausted global budget and explicit cancellation are the only run-ending
  conditions;
- compact and large-plan guidance selects progress without requiring
  unnecessary delegation; and
- existing visual proof and analysis-feedback contracts remain intact.

Focused artifact and prompt tests run before the authoritative `make verify`
gate. No new dependency, phase, command, or persistent runtime state is added.

## Evidence basis and limits

The design uses primary research only for the narrow behavior each source
measured; benchmark numbers are not guarantees for Ralph's agents.

- [Agentless](https://arxiv.org/abs/2407.01489) reports that a fixed
  localization, repair, and validation workflow outperformed the compared
  open-source agents on its then-current SWE-bench Lite evaluation while
  costing less. This supports following an explicit route with less
  discretionary re-exploration; it does not establish that every plan is
  correct.
- [ReAct](https://arxiv.org/abs/2210.03629) found that interleaved reasoning
  and environment actions help track and update plans and handle exceptions on
  its evaluated tasks. This supports the ready-item action/evidence loop and
  evidence-triggered local adaptation, not transferring its benchmark gains to
  coding.
- [Large Language Models Cannot Self-Correct Reasoning
  Yet](https://arxiv.org/abs/2310.01798) found that intrinsic self-correction
  often failed or degraded reasoning, while
  [CRITIC](https://arxiv.org/abs/2305.11738) found gains from tool-interactive
  feedback on its evaluated tasks. This supports focused checks and full-gate
  evidence rather than unaided self-review; it does not prove a separate model
  is always required.
- [Reflexion](https://arxiv.org/abs/2303.11366) improved subsequent trials by
  retaining feedback-derived reflection. This supports recording a bounded
  failure, cause, and changed next action, not unbounded history.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) shows that retrieval
  from long contexts depends materially on information position. This supports
  a short, single-sourced execution loop adjacent to the plan; it did not test
  coding-agent developer prompts.
- [Measuring AI Ability to Complete Long Software
  Tasks](https://arxiv.org/abs/2503.14499) associates longer task horizons
  with reliability and adapting to mistakes, subject to external-validity
  limits. This supports independently verifiable increments and bounded
  recovery, not a universal duration cutoff.
- [PlanBench](https://arxiv.org/abs/2206.10498) found substantial planning and
  change-reasoning limitations in its evaluated models and domains. This
  supports evidence-triggered local reconciliation while retaining the request
  as authority; it does not license wholesale replanning.
- [SWE-bench](https://arxiv.org/abs/2310.06770) characterizes repository issue
  resolution as coordinating changes across functions, classes, and files,
  while [SWE-agent](https://arxiv.org/abs/2405.15793) shows that the
  agent-computer interface materially changes measured coding performance.
  These support dependency-aware groups, concrete tool actions, and cross-unit
  checks; their historical success rates are not current performance claims.

The evidence supports the execution shape but cannot prove that this exact
prompt delivers timely completion. That product claim requires an agent
evaluation across compact, inaccurate, adapted, blocked, and large work-unit
plans, including the weakest supported agent.

## Prompt-to-evidence traceability

Every normative prompting behavior introduced by this design has an explicit
research basis and a bounded interpretation:

| Prompt behavior | Evidence basis | Supported inference |
|---|---|---|
| Follow the supplied plan and avoid broad rediscovery | Agentless; SWE-agent | A constrained localization/repair interface can reduce discretionary search; this is not evidence that plans are infallible. |
| Process the next ready dependency and verify each increment | ReAct; SWE-bench | Interleaved actions and observations support state tracking across repository changes. |
| Adapt only when fresh tool evidence falsifies the immediate premise | ReAct; PlanBench; CRITIC | Plans can be wrong, and external feedback is a stronger correction signal than unaided reconsideration. |
| Record a bounded failure, cause, and changed next action | Reflexion | Retained feedback can improve a later attempt; the evidence does not support unlimited retry history. |
| Keep shared guidance short and adjacent to the active plan | Lost in the Middle | Long-context retrieval is position-sensitive, so critical instructions should be concise and salient. |
| Use independent work units concurrently only when coordination is worthwhile | SWE-bench; SWE-agent; long-software-task measurements | Multi-file tasks benefit from explicit interfaces and verifiable decomposition; mandatory delegation for compact work is not supported. |
| Require focused tool evidence and a final repository gate | CRITIC; Cannot Self-Correct Yet | External feedback is safer than relying on intrinsic self-correction alone. |
| Have analysis independently re-derive criteria and dispositions | CRITIC; Cannot Self-Correct Yet | A separate evidence pass reduces reliance on an implementer's unsupported self-assessment; it does not guarantee correctness. |
| Approve partial/failed handoffs when fresh evidence shows no necessary work remains | ReAct; CRITIC | Outcome should follow observed task state, not the producer's label. |
| Retry only actionable localized gaps inside one cycle; close the cycle and replan impossible or non-evaluable outcomes | PlanBench; long-software-task measurements; Reflexion | Replanning and recovery are fallible and should be bounded; repeating unchanged work without a new evidence-based action is unsupported, while a fresh plan may expose a different route. |

Artifact grammar, stable IDs, routing names, and commit mechanics are local
workflow constraints rather than empirical claims about model cognition. They
are covered by repository tests and policy verification, not attributed to AI
research.
