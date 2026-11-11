---
name: submit-development-result-artifact
description: Use when submitting a development_result artifact as markdown via ralph_submit_md_artifact as a free-form body, or when a completed result was rejected for a malformed status value
version: 3.0.0
---

# submit-development-result-artifact

## Overview

A development result is one markdown document
(`artifact_type: "development_result"`) reporting what was done, which
files changed, and the verification behind the change. The frontmatter
`status` is the only field the validator mechanically checks; the body
is the next agent's reading matter, not a structure the artifact gates on.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

Frontmatter: `type: development_result` and exactly one closed-vocabulary
status: `completed`, `partial`, or `failed`. Any other status is invalid and
must be repaired before submission.

`status: completed` means the plan's intended outcomes are done and
verified, described in the author's own words; coverage of plan items
is judged by development analysis, not gated here. Completing only
some items is incremental progress, not completion.

`status: partial` and `status: failed` are accepted at any point. Use
`partial` when verified progress exists and remaining required work cannot
be completed by any developer action available in the current run; use
`failed` when no safe actionable continuation exists. Neither is a
shortcut, and neither decides whether the run ends. A developer who still
owns independent ready slices dispatches them in parallel rather than
handing each one back as a separate `partial`.

## Free-Form Body

There is no required section, no required field label, and no required
stable ID for individual items. The validator mechanically checks only
the frontmatter `status` enum; everything else is read by the next agent.
A useful shape is:

- One item under `## Summary` describing the outcome in plain language.
- One `## Files Changed` block listing the paths you touched.
- For each affected criterion or plan item, a short evidence line citing
  a reproducible command, a `path:line` anchor, or a focused test
  result.
- For `partial` or `failed`, state the concrete external action or
  in-scope continuation the next agent should pick up. The developer
  who still owns independent ready slices dispatches them as parallel
  sub-agents rather than handing each one back as a separate `partial`.

Coverage of the plan and of the prior analysis is judged by development
analysis, not checked mechanically here.

## Disposition Vocabulary

The free-form contract does not require per-step `Disposition:`
fields or per-finding `Rationale:` lines. Write the disposition in
your own words in the body if it matters, and let the next analysis
verdict decide. There is no `## Plan Items Proven` or
`## Analysis Items Addressed` shape to satisfy; an analysis verdict
that disagrees is itself the proof check.

## Frontmatter

- `type` — required; `development_result`.
- `status` — required and closed: `completed`, `partial`, or `failed`. Any
  other value, including `done` or `wrong`, is a hard error. The
  diagnostic names all accepted values; correct the frontmatter and
  resubmit.
