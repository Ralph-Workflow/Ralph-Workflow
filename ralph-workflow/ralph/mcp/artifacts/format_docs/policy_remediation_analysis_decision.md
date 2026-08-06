# policy_remediation_analysis_decision artifact format

Report whether each declared policy fact, command, marker, and script holds up
when probed. Submit markdown with `ralph_submit_md_artifact`
(`artifact_type: policy_remediation_analysis_decision`).

## Completed example

```markdown
---
type: policy_remediation_analysis_decision
status: completed
---

## Summary

- [SUM-1] Every declared criterion has evidence; no counterexample was found. Evidence: `make verify` exits 0.
```

## Request-changes example

```markdown
---
type: policy_remediation_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] One declared command is not met.

## What Came Up Short

- [PR-001] Criterion: the declared verification command resolves. Expected observation: `make verify-all` exits after invoking a target. Verdict: not met. Evidence: make reports no rule for `verify-all`. Location: verification-policy.md RALPH-COMMAND.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## What Came Up Short` is required and non-empty for `request_changes`
  and `failed`, omitted for `completed`.
- Each finding has a unique stable ID and states `Criterion:`, `Expected
  observation:`, `Verdict:`, `Evidence:`, and `Location:`.
- `## How To Fix` is optional for compatibility; verification decisions omit
  it. A working gate that exposes a product failure is attribution, not a
  policy shortfall.

`status` is `completed`, `request_changes`, or `failed`. `completed` cannot
contain findings; `not evaluable` findings require `failed` rather than
completion.

See `.agent/artifact-formats/examples/policy_remediation_analysis_decision.md` for the validator-backed complete example.

A `completed` decision that includes either remediation section is a hard error. A status outside `completed`, `request_changes`, or `failed` (including `done` or `wrong`) is a hard error.
