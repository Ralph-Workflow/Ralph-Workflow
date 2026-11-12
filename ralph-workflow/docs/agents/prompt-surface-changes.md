# Prompt surface contract

The prompt surface is the set of rendered templates that carry the
verified-delivery goal into an agent's context. This page states what that
surface is and where each contract is enforced.

## Surfaces and enforcement

| Surface | Contract | Enforcement |
| --- | --- | --- |
| `shared/_verification_commitments.j2` and the templates that include it | The verified-delivery goal, the precedence rule, and the four commitments are stated identically in every phase; the shared block names workflow-owned phase order, waits, and grants rather than claiming the templates configure them. | `ralph/testing/audit_prompt_single_sourcing.py` literal-checks the three deliberate cross-surface statements and is a mandatory `make verify` step that fails if either surface lacks a canonical statement. |
| `shared/_developer_iteration_guidance.j2`, `shared/_run_budget.j2`, and the development templates | Reach a cheap falsifiable increment first, honour the applicable gate, bound exploration and retries, and report an honest partial outcome when the run budget is spent. The guidance records independent work, avoids re-fetching held material, and sets the sync point at one proven increment. | Template render-integrity validates the included templates; the fenced-artifact example audit checks the retained submission examples. |
| `development_analysis.jinja` | Add fresh evidence, test assumptions, and return criterion-level findings. | Render-integrity and artifact-example audits keep the prompt and the validator-facing artifact grammar aligned. |
| `planning.jinja` | The work framing comes before the planning mechanics, the stable thinking instructions come before the submission mechanics, and the volatile request payload is last. | `tests/test_planning_prompt_thinking_first.py` checks request framing before `PLANNING MODE`, stable thinking before submission mechanics, and the payload last. |
| `docs/ralph-workflow-policy/verification-policy.md` and `ralph/project_policy/starters/verification-policy.md` | Fast, complete verification is a durable project obligation: 10-second fast path, recorded full-gate figure, recurring-cost ownership, actionable failures, and durable orientation. The starter carries the same obligations into projects that adopt it. | `audit_prompt_single_sourcing` checks the shared wording; `make verify` exercises the documented gate and policy checks. |
| `ralph/pipeline/run_time_report.py` and its markdown artifact contract | A bounded, comparable report of elapsed time, phases, signals, and slowest steps. This is reporting machinery, not an additional standard-setting surface. | `tests/pipeline/test_run_time_report.py` covers the generated report and its bounds. |

## Instruction funding

Obligation-style bullets and standalone imperatives are counted at a
rendered include site; a shared partial counts once at each site. The
retained instructions change agent or project behaviour: the shared
commitments select a fast path and require the full gate; developer
guidance bounds retrieval, retries, and waits; review guidance requires
independent evidence; policy text makes gate cost actionable.

`_verification_commitments.j2` is the single source of the goal, the
precedence rule, and the four commitment names for the review, analysis,
development, and fix prompts. `_developer_iteration_guidance.j2` is the
single source of the execution directions for the iteration templates.
Planning submission mechanics is one shared block rather than a copy per
variant. The shared block's workflow-boundary sentence names the
workflow-owned phase order, wait permissions, and phase grants; the
templates do not configure them.

## Output-shape consumers

`_artifact_submission.j2` routes submitted Markdown through its validator and
completion gate; the analysis templates feed their choice formats and
validators. Render-integrity and fenced-artifact-example audits cover these
rendered relationships.

## Single-audience developer prompts

Developer prompts have **one audience**: the single developer agent.
They must not branch on a worker or coordinator role. Specifically:

- Templates must not reference `IS_WORKER`, `is_worker`, or any worker-role variable
- No `{% if IS_WORKER %}` or `{% if not IS_WORKER %}` blocks
- No "worker scope", "coordinator dispatch", or "assigned unit" wording that assumes Ralph-orchestrated workers
- Parallelism guidance is unconditional and always rendered

Ralph Workflow's worker-orchestration model is deprecated; parallelism is owned
by the developer agent's own sub-agents. See
`docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'`
for the routing contract.
