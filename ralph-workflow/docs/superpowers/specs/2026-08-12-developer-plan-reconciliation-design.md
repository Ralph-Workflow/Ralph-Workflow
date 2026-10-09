# Developer plan reconciliation

> **SUPERSEDED** -- This design introduced a closed `Disposition:` vocabulary
> (`completed` / `adapted` / `not_applicable` / `blocked`), canonical-ID proof
> coverage, and compact-vs-large execution guidance for the development
> artifact. The current contract (see
> `ralph/mcp/artifacts/format_docs/development_result.md` and
> `ralph/mcp/artifacts/format_docs/development_analysis_decision.md`) is
> **free-form**: the validator mechanically checks only the frontmatter
> `status` enum, the body is the next agent's reading matter, and parallel
> work is the default. The text below is preserved as the historical
> narrative of the disposition iteration and no longer reflects current
> requirements. Do not treat any rule in this document as a current
> obligation.

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

The disposition-based reconciliation rules and evidence contracts originally defined here
have been removed. The current contract is free-form: see
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
premise, selects a disposition, implements the smallest useful increment, runs
the narrowest relevant proof, records evidence, and moves to the next ready
item. After a failed approach it changes the hypothesis or route; it does not
repeat the same attempt without new evidence. When no necessary item can make
progress, it submits a partial result with the blocker and next step.

Continuation prompts preserve this same execution, reconciliation, verified
delivery, and run-budget contract. Prior partial narrative is handoff context,
not proof.

## Analyzer behavior

Development analysis answers whether the implementation satisfies every unchanged request
and plan acceptance criterion using fresh evidence.

The analyzer independently evaluates the request, plan, and current workspace.
It must not use the implementer's summary, rationale, proof, or completion claim
as evidence. A literal departure from an inaccurate implementation route is not
itself a defect. Uncovered necessary work requires changes.

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

The original disposition field and required proof structure have been removed.
See `ralph/mcp/artifacts/format_docs/development_result.md` for the current contract.

## Verification design

Black-box tests cover:

- valid status with free-form markdown body parses through the public artifact seam;
- initial, continuation, worker, and fallback prompts share the execution
  loop and do not duplicate divergent rules;
- the analyzer independently audits the implementation against request criteria;
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
| Have analysis independently re-derive criteria | CRITIC; Cannot Self-Correct Yet | A separate evidence pass reduces reliance on an implementer's unsupported self-assessment; it does not guarantee correctness. |
| Approve partial/failed handoffs when fresh evidence shows no necessary work remains | ReAct; CRITIC | Outcome should follow observed task state, not the producer's label. |
| Retry only actionable localized gaps inside one cycle; close the cycle and replan impossible or non-evaluable outcomes | PlanBench; long-software-task measurements; Reflexion | Replanning and recovery are fallible and should be bounded; repeating unchanged work without a new evidence-based action is unsupported, while a fresh plan may expose a different route. |

Artifact grammar, stable IDs, routing names, and commit mechanics are local
workflow constraints rather than empirical claims about model cognition. They
are covered by repository tests and policy verification, not attributed to AI
research.
