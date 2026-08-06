---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.1.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. Submit one mandatory executor-ready plan: stable `### [S-n] Title` steps, allowed `Type`, concrete targets or discovery location, real dependencies, and per-step proof.

## Author and submit

1. Ground the outcome, current behavior, target files, risks, and proof in repository evidence.
2. Cover Orient, Characterize, Change, and Verify. Use a discovery step for an unknown rather than inventing a path or command.
3. Optionally check with `ralph_verify_md_artifact`, then submit with `ralph_submit_md_artifact` using `artifact_type: plan` and the full text.
4. For a similar revision, use `ralph_edit_md_artifact` on the staged draft; it submits when valid. Use `ralph_stage_md_artifact` with `replace_all` only for a wholesale rewrite. `ralph_get_md_draft` inspects the draft and `ralph_finalize_md_artifact` submits an assembled staged draft.
5. `ralph_discard_md_draft` is only for a genuine wholesale restart.

Worked example:

```markdown
---
type: plan
---

## Work

### [S-1] Characterize the retry default
Inspect the current retry behavior and add a focused regression before changing it.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Verify: pytest tests/test_retry.py -q
Expect: the regression fails before the change

### [S-2] Change and prove the timeout behavior
Implement the smallest timeout change, then run the focused regression.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0

## Verification
- [V-1] pytest tests/test_retry.py -q
  Expect: the focused retry tests pass with exit code 0
```

Use `ralph_verify_md_artifact` before submission when a fast diagnostic preview helps. Use `ralph_stage_md_artifact`, `ralph_get_md_draft`, and `ralph_finalize_md_artifact` for an assembled draft; use `ralph_discard_md_draft` only for a genuine wholesale restart.

`schema_version` and `## Validation Overrides` are unsupported. Repair every diagnostic directly. The only step-less document is exactly `type: plan` plus `noop: true`.
