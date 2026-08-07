# development_analysis_decision artifact format

Report whether each criterion fixed by the request and plan is met. Submit
markdown with `ralph_submit_md_artifact`
(`artifact_type: development_analysis_decision`).

## Completed example

```markdown
---
type: development_analysis_decision
status: completed
---

## Summary

- [SUM-1] No counterexample was found for the fixed criteria.

## Criterion Verdicts

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: met. Evidence: `pytest tests/test_feature.py -q` reports 12 passed. Location: tests/test_feature.py:42.
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

## Criterion Verdicts

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: not met. Evidence: `pytest tests/test_foo.py -q` has no oversized-index case. Location: tests/test_foo.py.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## Criterion Verdicts` is required and non-empty for every decision. Each
  item has a unique `DA-###` ID and `Criterion:`, `Expected observation:`,
  `Verdict:`, non-empty `Evidence:`, and non-empty `Location:` fields. Every
  non-met verdict has a same-ID mirror in `## What Came Up Short`.
- `## What Came Up Short` is required and non-empty for `request_changes` and
  `failed`; it mirrors localized non-met criterion verdicts and is omitted for
  `completed`.
- `## How To Fix` is not permitted. `## Analysis Items Addressed` cites the
  stable finding ID as its closure reference, not a remedy authored by the
  verifier.

`status` is `completed`, `request_changes`, or `failed`. `met` means no
counterexample was found. `not evaluable` requires `failed` rather than
completion.

See `.agent/artifact-formats/examples/development_analysis_decision.md` for the
validator-backed complete example.
