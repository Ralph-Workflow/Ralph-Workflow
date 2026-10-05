# planning_analysis_decision artifact format

Report whether the plan is ready to execute. Submit markdown with
`ralph_submit_md_artifact` (`artifact_type: planning_analysis_decision`).

The analyzer assesses substance, not plan shape. `status` is `completed`,
`request_changes`, or `failed`. Its five criteria are coverage, truthfulness,
actionability, parallel decomposition, and execution conflicts. For avoidable
serialization, return `request_changes`, name the independent branches, and put
a concrete proposed unit split in the finding's `Proposed revision:`. Each
proposed unit names the files or directories it owns, its own focused check,
and is sized for one agent. Use only evidenced prerequisites: shared-contract
producers precede their consumers, and one integration step follows fan-in.
The planner will revise from this feedback, so every non-completed verdict and
its mirrored finding carries a concrete `Proposed revision:` the planner either
applies or rebuts; the validator enforces this on submission (`ANALYSIS019`).
Do not add a `## How To Fix` section: the revision lives inline.
These decision-artifact requirements do not impose a format on the plan.

## Review criteria

- **Coverage:** every part of the request is addressed.
- **Truthfulness:** paths, commands, and current-behavior claims match fresh
  repository evidence.
- **Actionability:** the executor knows what to change and how to show it works.
- **Parallel decomposition:** independent work can run concurrently; list order
  or invented dependencies do not substitute for actual coupling.
- **Execution conflicts:** concurrent units do not write the same file, and
  consumers follow their shared-contract producer.

Keep analysis read-only. Delegate independent checks to read-only subagents,
then reproduce every relied-on lead in the main session. The planner's account
is not proof. Record evidence for every criterion; the excerpt below illustrates
one non-met criterion rather than a complete review of a real repository.

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
  `Location:`, and `Cost:`, and records criterion-level evidence. On a
  non-completed decision every verdict needs `Proposed revision:` (`none`
  for a met criterion); a completed decision does not need one because the
  plan was approved as-is. A `not evaluable` criterion requires `failed`
  rather than completion.
- `## What Came Up Short` is required for `request_changes` and `failed`;
  it mirrors localized non-met verdicts, each with its `Proposed revision:`,
  which contains the proposed unit split when parallel decomposition is not
  met.
- `## How To Fix` is not permitted.

See `.agent/artifact-formats/examples/planning_analysis_decision.md`.
