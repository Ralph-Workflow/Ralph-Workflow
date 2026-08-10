# planning_analysis_decision artifact format

Report whether each fixed request and plan criterion is executor-ready. Submit
markdown with `ralph_submit_md_artifact`
(`artifact_type: planning_analysis_decision`).

## Completed example

```markdown
---
type: planning_analysis_decision
status: completed
---

## Summary

- [SUM-1] No counterexample was found for the fixed criteria.

## Criterion Verdicts

- [PA-001] Step: [S-2] Criterion: S-2 provides a runnable verification command. Expected observation: the command resolves in this repository. Verdict: met. Evidence: `pytest tests/test_plan.py -q` reports 12 passed. Location: S-2 Verify field.
```

## Request-changes example

```markdown
---
type: planning_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] One fixed criterion is not met.

## What Came Up Short

- [PA-001] Step: [S-2] Criterion: S-2 provides a runnable verification command. Expected observation: the command resolves in this repository. Observation: the command does not resolve. Verdict: not met. Evidence: `pytest tests/missing.py -q` reports a missing path. Location: S-2 Verify field. Cost: the executor cannot run the promised proof.

## Criterion Verdicts

- [PA-001] Step: [S-2] Criterion: S-2 provides a runnable verification command. Expected observation: the command resolves in this repository. Observation: the command does not resolve. Verdict: not met. Evidence: `pytest tests/missing.py -q` reports a missing path. Location: S-2 Verify field. Cost: the executor cannot run the promised proof.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## Criterion Verdicts` is required and non-empty for every decision. Each
  item has a unique `PA-###` ID, `Step: [S-n]` or `Plan-level:`, and
  `Criterion:`, `Expected observation:`, `Observation:`, `Verdict:`,
  non-empty `Evidence:`, non-empty `Location:`, and `Cost:` fields. Every
  non-met verdict has a same-ID mirror in `## What Came Up Short`.
- `## What Came Up Short` is required and non-empty for `request_changes` and
  `failed`; it mirrors localized non-met criterion verdicts and is omitted for
  `completed`.
- `## How To Fix` is not permitted. A later phase uses the stable finding ID,
  not a verifier-authored remedy, as its closure reference.

`status` is `completed`, `request_changes`, or `failed`. `met` means no
counterexample was found. `not evaluable` requires `failed` rather than
completion.

See `.agent/artifact-formats/examples/planning_analysis_decision.md` for the
validator-backed complete example.
