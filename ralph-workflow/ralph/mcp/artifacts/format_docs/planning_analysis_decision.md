# planning_analysis_decision artifact format

Report whether the plan is ready to execute. Submit markdown with
`ralph_submit_md_artifact` (`artifact_type: planning_analysis_decision`).

The analyzer assesses substance, not plan shape: a plain prose plan can
pass. `status` is `completed`, `request_changes`, or `failed`. Its five
criteria are coverage, truthfulness, actionability, parallel decomposition,
and execution conflicts. The planner will revise from this feedback, so
each finding must carry a concrete proposed revision the planner either
applies or rebuts; do not just grade. For avoidable serialization, return
`request_changes` and put a concrete proposed unit split in the finding's
`Proposed revision:`. Do not add a `## How To Fix` section.

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
  `Location:`, and `Cost:`, and records criterion-level evidence. A
  `not evaluable` criterion requires `failed` rather than completion.
- `## What Came Up Short` is required for `request_changes` and `failed`;
  it mirrors localized non-met verdicts and each mirrored finding carries
  its `Proposed revision:` so the planner can apply or rebut it on the next
  pass.
- `## How To Fix` is not permitted.

See `.agent/artifact-formats/examples/planning_analysis_decision.md`.
