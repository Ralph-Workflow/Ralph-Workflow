# Prompt surface change record

This is the focused record for the speed-of-verified-delivery increment: commits
`a92a31941` through `cf800b26f`, plus the planning-template order correction.
The stated (S) and recorded (R) criteria are complete and verified. The baseline
(B) is frozen. The observed (O) criteria are staged but unrun: they require the
operator-owned measurement campaign defined by the baseline and are not claimed
by this unattended in-repository pass.

## Scope, effect, and proof

| Surface | Criteria | Expected effect | Evidence and actual outcome |
| --- | --- | --- | --- |
| `shared/_verification_commitments.j2` and the templates that include it | S1, S2, S6, S10, S11, S15, S28 | Keep the verified-delivery goal, precedence rule, and four commitments salient in each phase without phase-local variants. | `audit_prompt_single_sourcing` literal-checks the three deliberate cross-surface statements. The shared block names workflow-owned phase order, waits, and grants rather than claiming the templates configure them. |
| `shared/_developer_iteration_guidance.j2`, `_run_budget.j2`, and development/fix templates | S3–S5, S7–S9, S12–S21 | Reach a cheap falsifiable increment first, honour the applicable gate, bound exploration and retries, and report an honest partial outcome when the run budget is spent. | Template render-integrity validates included templates; the fenced-artifact example audit checks the retained submission examples. The guidance records independent work, avoids re-fetching held material, and sets the sync point at one proven increment. |
| `review.jinja`, `development_analysis.jinja`, and `review_analysis.jinja` | S22, S26 | Make review add fresh evidence, read assumptions, resolve safe trivia where permitted, and return material findings with cost and fix. | Render-integrity and artifact-example audits keep the prompt and validator-facing artifact grammar aligned. The output requirements name the issues or decision-artifact consumer. |
| `planning.jinja` | S23, S24 | Put the work framing before planning mechanics while keeping stable instructions before the volatile request payload. | `tests/test_planning_prompt_thinking_first.py` checks request framing before `PLANNING MODE`, stable thinking before submission mechanics, and the payload last. |
| `docs/ralph-workflow-policy/verification-policy.md` and `ralph/project_policy/starters/verification-policy.md` | S1–S3, S6–S10, S13, S28 | Make fast, complete verification a durable project obligation: 10-second fast path, recorded full-gate figure, recurring-cost ownership, actionable failures, and durable orientation. | `audit_prompt_single_sourcing` checks shared wording; `make verify` exercises the documented gate and policy checks. The starter preserves the same obligations for projects that adopt it. |
| `ralph/testing/audit_prompt_single_sourcing.py` and `ralph/verify.py` wiring | R1, R2, S27 | Prevent the goal, precedence rule, and four commitment names from drifting between the two surfaces. | The audit is a mandatory `make verify` step and fails if either surface lacks a canonical statement. |
| `ralph/pipeline/run_time_report.py` and its markdown artifact contract | O9 reporting surface | Leave a bounded, comparable report of elapsed time, phases, signals, and slowest steps. | `tests/pipeline/test_run_time_report.py` covers the generated report and its bounds. This is reporting machinery, not an additional standard-setting surface. |

## Instruction funding and retention (R1–R3)

Method: count distinct obligation-style bullets and standalone imperatives at a
rendered include site; shared partials count once at each site. The retained
instructions change agent or project behaviour: the shared commitments select a
fast path and require the full gate; developer guidance bounds retrieval,
retries, and waits; review guidance requires independent evidence; policy text
makes gate cost actionable.

The additions were funded by consolidation rather than new phase-specific
copies. `_verification_commitments.j2` replaced separate goal/precedence and
commitment wording in review, analysis, development, and fix prompts.
`_developer_iteration_guidance.j2` replaced scattered execution directions in
iteration templates. Planning submission mechanics remains one shared block
rather than being copied into variants. Superseded duplicated wording was
removed from those templates as the shared blocks landed; no new template,
phase, output grammar, dependency, or verification check was added.

The planning-order correction adds no instruction: it moves the existing
request framing before mechanics and keeps the existing payload at the volatile
tail. The focused regression test retains that ordering. The shared block's
workflow-boundary sentence is intentionally retained because removing it would
make templates imply control over phase order, wait permissions, or phase grants
that belongs to runtime machinery.

## Sampling and boundaries

Sampled consumers for retained output shape: `_artifact_submission.j2` routes
submitted Markdown through its validator and completion gate; `review.jinja`
feeds the `issues` format and validator; analysis templates feed their decision
formats and validators. Render-integrity and fenced-artifact-example audits
cover these rendered relationships.

No check was removed or weakened; the full gate's proof inventory is unchanged.
Phase existence and order, wait permissions, and per-phase capability or
thinking grants remain runtime-owned and are explicitly described as such in the
shared commitments and policy. This record does not claim the brief's baseline
or observed measurements, which need the separately agreed task-set harness.

## Verification

Expected: prompt ordering makes the work legible before mechanics, shared
statements stay identical, and the docs build has no warning. Actual: `make
verify` passed; it ran Sphinx, lint, type checking, render-integrity,
artifact-example, prompt-single-sourcing, and the remaining mandatory audits.
Its combined test elapsed time was 37.90 seconds of the 60-second budget.
