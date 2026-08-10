# policy_remediation_analysis_decision artifact format

Report whether every declared policy fact, command, marker, and script holds up
when probed. Submit markdown with `ralph_submit_md_artifact`
(`artifact_type: policy_remediation_analysis_decision`).

## Completed example

```markdown
---
type: policy_remediation_analysis_decision
status: completed
---

## Summary

- [SUM-1] No counterexample was found for the declared criteria.

## Criterion Verdicts

- [PR-001] Criterion: the declared verification command resolves. Expected observation: `make verify` exits 0. Verdict: met. Evidence: `make verify` exits 0. Location: verification-policy.md RALPH-COMMAND.
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

## Criterion Verdicts

- [PR-001] Criterion: the declared verification command resolves. Expected observation: `make verify-all` exits after invoking a target. Verdict: not met. Evidence: make reports no rule for `verify-all`. Location: verification-policy.md RALPH-COMMAND.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## Criterion Verdicts` is required and non-empty for every decision. Each
  item has a unique `PR-###` ID and `Criterion:`, `Expected observation:`,
  `Verdict:`, non-empty `Evidence:`, and non-empty `Location:` fields. Every
  non-met verdict has a same-ID mirror in `## What Came Up Short`.
- `## What Came Up Short` is required and non-empty for `request_changes` and
  `failed`; it mirrors attributable localized non-met criterion verdicts and is
  omitted for `completed`.
- `## How To Fix` is not permitted. A working gate that exposes a product
  failure is attribution, not a policy shortfall.

`status` is `completed`, `request_changes`, or `failed`. `met` means no
counterexample was found. `not evaluable` requires `failed` rather than
completion.

See `.agent/artifact-formats/examples/policy_remediation_analysis_decision.md`
for the validator-backed complete example.
