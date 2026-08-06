# development_analysis_decision artifact format

Report whether the implementation meets criteria fixed by the request and plan.
Submit markdown with `ralph_submit_md_artifact`
(`artifact_type: development_analysis_decision`).

## Completed example

```markdown
---
type: development_analysis_decision
status: completed
---

## Summary

- [SUM-1] Every fixed criterion has evidence; no counterexample was found. Evidence: `pytest tests/test_feature.py -q` reports 12 passed.
```

## Request-changes example

```markdown
---
type: development_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] One fixed criterion is not met.

## What Came Up Short

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: not met. Evidence: `pytest tests/test_foo.py -q` has no oversized-index case. Location: tests/test_foo.py.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## What Came Up Short` is required and non-empty for `request_changes`
  and `failed`, omitted for `completed`.
- Each finding has a unique stable ID and states `Criterion:`, `Expected
  observation:`, `Verdict:`, `Evidence:`, and `Location:`.
- `## How To Fix` is optional for compatibility; verification decisions omit
  it. `## Analysis Items Addressed` cites the finding ID as its closure
  reference, not a remedy authored by the verifier.

`status` is `completed`, `request_changes`, or `failed`. `completed` cannot
contain findings; `not evaluable` findings require `failed` rather than
completion.

See `.agent/artifact-formats/examples/development_analysis_decision.md` for the validator-backed complete example.

A `completed` decision that includes either remediation section is a hard error. A status outside `completed`, `request_changes`, or `failed` (including `done` or `wrong`) is a hard error.
