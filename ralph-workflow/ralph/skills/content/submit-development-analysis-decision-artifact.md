---
name: submit-development-analysis-decision-artifact
description: Use when submitting a development_analysis_decision artifact as markdown via ralph_submit_md_artifact as a free-form body, or when a verdict was rejected for a malformed status value
version: 1.0.0
---

# submit-development-analysis-decision-artifact

## Overview

A development analysis decision is one markdown document
(`artifact_type: "development_analysis_decision"`) recording the
reviewer's verdict on the work the development iteration produced. The
frontmatter `status` is the only field the validator mechanically checks;
the body is the next agent's reading matter, not a structure the artifact
gates on.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

Frontmatter: `type: development_analysis_decision` and exactly one
closed-vocabulary status: `completed`, `request_changes`, or `failed`. Any
other status is invalid and must be repaired before submission.

- `status: completed` means the reviewer accepts the iteration as written.
- `status: request_changes` means the reviewer found shortfalls; the body
  should describe what fell short, what evidence shows it, and how the
  leftover work should be split into independent units the next agent can
  dispatch in parallel.
- `status: failed` means the reviewer concludes no safe continuation
  exists; the body should name the literal impossibility and any external
  action required.

The pipeline routes on this enum, so the validator enforces it strictly.
Nothing else in the body is mechanically validated.

## Free-Form Body

There is no required section, no required field label, and no required
stable ID for individual items. The validator mechanically checks only
the frontmatter `status` enum; everything else is read by the next agent.
A useful shape is:

- A short summary of the verdict in plain language.
- Evidence lines for each shortcoming, citing a reproducible command, a
  `path:line` anchor, or a focused test result.
- For `request_changes`, a planner-style fix plan that re-splits the
  remaining work into independent units with clear ownership and per-unit
  checks. The original plan's split might not suit what is left.
- For `failed`, the concrete external action or in-scope continuation the
  next agent should pick up, and a statement of why no developer action
  available in the current run can advance the plan.

Whole-change review (integration, regressions, scope, repository policy)
remains judgment in the prompt, not a validated field. The shared
`submit-artifact` skill describes which artifact types keep the closed
grammar; this skill teaches the contract for the free-form development
analysis decision.

## Frontmatter

- `type` — required; `development_analysis_decision`.
- `status` — required and closed: `completed`, `request_changes`, or
  `failed`. Any other value, including `done` or `wrong`, is a hard
  error. The diagnostic names all accepted values; correct the
  frontmatter and resubmit.
