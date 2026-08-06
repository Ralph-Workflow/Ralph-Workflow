# plan artifact format

A plan is the executor's instruction set. Every active plan uses stable `### [S-n] Title` steps. The only step-less form is exactly `type: plan` with `noop: true`.

Each step has a `Type` from `file_change`, `file_create`, `file_delete`, `refactor`, `config_change`, `discovery`, or `verify`.

- Work steps require `Files`, a concrete `Verify`, and an observable `Expect`.
- `verify` requires `Verify` plus `Expect` or `Location`.
- `discovery` requires `Verify`, `Location`, or `Evidence`.
- Use `Depends on: S-n` only where ordering exists. `Satisfies`, `Rationale`, and `Evidence` add useful execution context.

Missing or inconsistent required structure blocks submission with a line- and step-anchored repair diagnostic. `schema_version` and `## Validation Overrides` are unsupported; repair the plan instead of bypassing validation.

## Example

```markdown
---
type: plan
---

## Work

### [S-1] Characterize current retry behavior
Inspect the current default and preserve it in a focused regression.

Type: discovery
Location: tests/test_retry.py

### [S-2] Change and prove the default
Update retry handling and run the focused regression.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0
```

Orient, Characterize, Change, and Verify are useful ordering guidance, not required document sections. For parallel work, use `## Work Units` or `## Parallel Plan` only when the executor will consume them.

## Submission

Submit the complete document with `ralph_submit_md_artifact` using `artifact_type: plan`. Use `ralph_edit_md_artifact` for a similar revision; it submits when the repaired draft validates. Staging is not submission. Do not write `.agent/artifacts/plan.md` directly. After a valid submission, call `declare_complete` as the final action.
