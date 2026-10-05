# planning_analysis_decision artifact format

Report whether the plan is ready to execute. Submit markdown with
`ralph_submit_md_artifact` (`artifact_type: planning_analysis_decision`).

The analyzer assesses substance, not plan shape: a plain prose plan can
pass. `status` is `completed`, `request_changes`, or `failed`. Its five
criteria are coverage, truthfulness, actionability, parallel decomposition,
and execution conflicts. The planner will revise from this feedback, so
each non-completed verdict and its mirrored finding must carry a concrete
proposed revision the planner either applies or rebuts; the validator
enforces this on submission (`ANALYSIS019`), so missing revisions are
rejected, not just suggested. Do not just grade. For avoidable
serialization, return `request_changes` and put a concrete proposed unit
split in the finding's `Proposed revision:`. Do not add a `## How To Fix`
section: the planner applies or rebuts each `Proposed revision:` inline on
the next pass, so a separate remediation section would split one rule
across two places and force the analyzer to re-derive the same fix in two
shapes.

## Request-changes example

```markdown
---
type: planning_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] Parallel decomposition is not met.

## What Came Up Short

- [PA-001] Plan-level: Criterion: parallel decomposition. Expected observation: independent documentation and test work can proceed concurrently. Observation: the plan serializes them without coupling. Proposed revision: split the work into a documentation unit owning Paths: docs/usage.md with the focused check markdownlint docs/usage.md and a test unit owning Paths: tests/test_usage.py with the focused check python -m pytest tests/test_usage.py -q, each sized for a single agent, then an integration step that fans the two units back in. Verdict: not met. Evidence: repository paths have no dependency. Location: plan prose. Cost: unnecessary elapsed time.

## Criterion Verdicts

- [PA-001] Plan-level: Criterion: parallel decomposition. Expected observation: independent documentation and test work can proceed concurrently. Observation: the plan serializes them without coupling. Proposed revision: split the work into a documentation unit owning Paths: docs/usage.md with the focused check markdownlint docs/usage.md and a test unit owning Paths: tests/test_usage.py with the focused check python -m pytest tests/test_usage.py -q, each sized for a single agent, then an integration step that fans the two units back in. Verdict: not met. Evidence: repository paths have no dependency. Location: plan prose. Cost: unnecessary elapsed time.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## Criterion Verdicts` is required and non-empty. Each item uses a stable,
  unique `PA-###` ID, carries `Criterion:`, `Expected observation:`,
  `Observation:`, `Proposed revision:`, `Verdict:`, `Evidence:`,
  `Location:`, and `Cost:`, and records criterion-level evidence. The
  planning contract treats the cost of the missed split as part of the
  verdict, so the validator enforces `Cost:` on every planning verdict
  (`ANALYSIS005`); a submission without it is rejected, not just
  suggested. A `not evaluable` criterion requires `failed` rather than
  completion. `Proposed revision:` is the concrete revision the planner
  applies or rebuts on the next pass; the validator enforces it on every
  non-completed planning verdict (`ANALYSIS019`), so submissions missing
  it are rejected. A completed decision with met verdicts does not need a
  revision because the plan was approved as-is.
- `## What Came Up Short` is required for `request_changes` and `failed`;
  it mirrors localized non-met verdicts and each mirrored finding carries
  its `Proposed revision:` so the planner can apply or rebut it on the next
  pass. The validator enforces the field on every finding via
  `ANALYSIS019`; submissions missing it are rejected.
- `## How To Fix` is not permitted.

See `.agent/artifact-formats/examples/planning_analysis_decision.md`.
