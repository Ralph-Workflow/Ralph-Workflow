# Development prompt recovery contract

This contributor reference explains how development prompts address large or
underestimated tasks without relaxing scope, worker isolation, or verification.
It describes instructions delivered to agents, not measured live-agent behaviour.
For verification commands and the full gate, see the repository-root
`docs/agents/verification.md`.

## Start with actionable work

The initial, continuation, fallback, and worker templates include the shared
`_developer_iteration_guidance.j2` recovery guidance once, before variable
request/plan payloads. Fallback applies to both main and worker roles, including
continuations and fallback after a primary-template render failure.

The recovery sequence applies even when the task looks huge before implementation:

1. Inventory the plan references and acceptance criteria without reducing scope.
2. Choose an immediately actionable, falsifiable increment before prolonged scope
   analysis.
3. In a main session, dispatch independent ready scopes within exposed capacity;
   workers stay within their assignment and never recursively dispatch.
4. Implement owned work and run its focused verification.
5. Recompute readiness after each result and continue with the next safe action.

A verified increment is a progress checkpoint, not permission to stop after one
slice. Size, uncertainty, underestimated scope, or a failed tactic calls for
scheduling or diagnosis. Retries require a changed, evidence-backed approach.

## Parallel waves and sequential recovery

`shared/_parallel_execution.jinja` defines exact path ownership and conflict
serialization in **Ownership**, and excluded coordination paths in **Scope
exclusions**. Its **Independent ready group (for linear plans too)** section
requires dependency-ready, pairwise-disjoint work to run concurrently within the
active worker limit. A full worker pool queues later work; it does not shrink the
request. The main session continues its own ready work, refills freed slots, and
reproduces verdict-bearing worker evidence before relying on it.

Coupled work or unavailable delegation requires sequential recovery on the
critical path, with focused proof and a new readiness check after each increment.
The agent must not invent tools or bypass brokered permissions to obtain
parallelism. Main sessions retain integration, shared decisions, and the full
repository-wide verification gate after all writers finish.

Dedicated and fallback worker prompts retain assigned scope, no recursive
dispatch, focused `Verify` commands, and worker-local artifact paths. Workers do
not take over whole-plan proof, another unit's files, or repository-wide checks.

## Incomplete outcomes and runtime limits

`shared/_no_exemption_for_failures.j2` is the canonical incomplete-result rule.
Difficulty, elapsed time, an exhausted budget, and scope size are not reasons to
submit `partial` while an available developer action can advance required work.
A claimed external blocker needs concrete evidence that the remaining work
cannot be completed through available developer actions; it is not a synonym for
a failed tactic. Completion and partial/failed artifacts must remain truthful.

These instructions do not override runtime deadlines or force cuts. If the
runtime actually ends the attempt, completed work and remaining obligations must
be reported accurately, never relabelled complete. Earlier descriptions of a
budget-spent partial result or a one-increment stopping point are superseded by
this contract. Historical timing figures are not evidence for the current tree;
rerun the checks for current measurements.

## Verification and evidence limits

`tests/test_prompts_no_exemption_for_failures.py` is the regression entry point
for delivered recovery guidance, ordering, and role boundaries.
`tests/test_developer_prompt_subagent_guidance.py` covers parallel ownership and
dispatch obligations. A template-source assertion checks source text only; it is
not evidence that a renderer delivered that instruction. Delivered-output tests
must exercise the relevant role and render path, including fallback, to support
claims about that surface.

Run the focused prompt tests and render-integrity audit when changing these
surfaces, then run `make verify` from `ralph-workflow/` after integration. The
full gate retains its immutable 60-second combined test budget. Passing tests
establish the asserted prompt contract, not universal drift detection, live-agent
obedience, or improved completion rates. Those outcomes require a separately
authorised live-agent evaluation; none is claimed here.
