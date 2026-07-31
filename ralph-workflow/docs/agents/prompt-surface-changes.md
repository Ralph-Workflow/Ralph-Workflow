# Prompt surface changes

This record covers the narrow prompt and policy increment in PLAN S-1 through
S-5. It is a change record for the reviewing phase, not a new policy surface.

## S26 sampling

Sampled the artifact and capability instructions that the changed execution and
judging phases render:

- `shared/_artifact_submission.j2` — consumer named: its format document,
  submission validator, and completion gate consume the document and receipt.
- `shared/_mcp_tools.jinja` — consumer named: the artifact format document,
  validator diagnostics, staged draft, and submission tool consume the stated
  arguments and edits.
- `fix_mode.jinja` — consumer named: its `fix_result` format document and
  completion gate consume the stated grammar and transport contract.
- `review.jinja` — consumer named: `.agent/artifact-formats/issues.md` and the
  issues-artifact validator consume the required headings, items, and statuses.
- `review_analysis.jinja` — consumer named: the
  `review_analysis_decision` format document and validator consume its decision
  shape; no extra output-shape instruction was added.

No sampled requirement lacked a consumer, so none was removed.

## Instruction count (R1)

Method: for each touched rendered surface, count distinct top-level bullet
items in its obligation-style lists plus standalone imperative sentences. An
included partial is counted once at each rendered include site. Baseline is the
pre-step revision; after is this change.

| Surface | Before | After | Funding |
| --- | ---: | ---: | --- |
| `review.jinja` | 32 | 38 | Required S-1 shared partial; reused rather than copied. |
| `review_analysis.jinja` | 77 | 83 | Required S-1 shared partial; reused rather than copied. |
| `fix_mode.jinja` | 13 | 21 | Required S-1/S-2 execution guidance; both existing partials are reused. |
| `shared/_verification_commitments.j2` | 5 | 7 | Required S-3 boundary; extends its existing boundary statement. |
| `docs/ralph-workflow-policy/verification-policy.md` default requirements | 14 | 16 | Required S-2 named commitments; the policy is the durable project example. |

The additions are either required by the named plan criteria or reuse the
existing shared partial. No new standalone prompt partial or output grammar was
introduced. The S-3 boundary adds two instructions; this required, unfunded
increase is recorded here for review because it names the workflow/runtime
boundary that the prompt otherwise left implicit.
