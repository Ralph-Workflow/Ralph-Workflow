# planning_analysis_decision artifact format

Report whether the fixed request and plan criteria are executor-ready. Submit
markdown with `ralph_submit_md_artifact`
(`artifact_type: planning_analysis_decision`).

## Completed example

```markdown
---
type: planning_analysis_decision
status: completed
---

## Summary

- [SUM-1] Every fixed criterion has evidence; no counterexample was found.
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
```

## Sections

- `## Summary` is required and has exactly one item.
- `## What Came Up Short` is required and non-empty for `request_changes`
  and `failed`, omitted for `completed`.
- Each finding has a unique stable ID and states `Criterion:`, `Expected
  observation:`, `Observation:`, `Verdict:`, `Evidence:`, `Location:`, and
  `Cost:`. Planning findings also identify `Step: [S-n]` or `Plan-level:`.
- `## How To Fix` is optional for compatibility; verification decisions omit
  it. The finding ID, not a verifier-authored remedy, is the closure reference.

`status` is `completed`, `request_changes`, or `failed`. `completed` cannot
contain findings; `not evaluable` findings require `failed` rather than
completion.

See `.agent/artifact-formats/examples/planning_analysis_decision.md` for the validator-backed complete example.

A `completed` decision that includes either remediation section is a hard error. A status outside `completed`, `request_changes`, or `failed` (including `done` or `wrong`) is a hard error.
