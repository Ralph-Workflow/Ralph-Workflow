---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.1.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. Submit one mandatory executor-ready plan: stable `### [S-n] Title` steps, allowed `Type`, concrete targets or discovery location, real dependencies, and per-step proof.

## Author and submit

1. Ground outcome, current behavior, target files, and proof in repository evidence.
2. Write self-contained steps in Orient, Characterize, Change, Verify order.
3. Work steps include `Files`, concrete `Verify`, and observable `Expect`; `verify` and `discovery` include their required proof or location.
4. Submit with `ralph_submit_md_artifact` using `artifact_type: plan`. For a similar revision, use `ralph_edit_md_artifact`; it submits when valid.
5. Use `ralph_discard_md_draft` only for a genuine wholesale restart.

Worked example:

```markdown
---
type: plan
---

## Work

### [S-1] Characterize the current behavior
Add a focused regression that demonstrates the current result.
Type: file_create
Files:
- create tests/test_feature.py
Verify: pytest tests/test_feature.py -q
Expect: the focused regression fails with exit code 1

### [S-2] Change and verify the behavior
Implement the smallest fix and rerun the focused regression.
Type: file_change
Files:
- modify ralph/feature.py
- modify tests/test_feature.py
Depends on: S-1
Verify: pytest tests/test_feature.py -q
Expect: the focused regression passes with exit code 0

## Verification
- [V-1] pytest tests/test_feature.py -q
  Expect: the focused regression passes with exit code 0
```

Use `ralph_verify_md_artifact` before submission when a fast diagnostic preview helps. Use `ralph_stage_md_artifact`, `ralph_get_md_draft`, and `ralph_finalize_md_artifact` for an assembled draft; use `ralph_discard_md_draft` only for a genuine wholesale restart.

`schema_version` and `## Validation Overrides` are unsupported. Repair every diagnostic directly. The only step-less document is exactly `type: plan` plus `noop: true`.
