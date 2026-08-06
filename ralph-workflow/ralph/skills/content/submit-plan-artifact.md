---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 3.0.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. Submit one mandatory executor-ready plan: stable `### [S-n] Title` steps, allowed `Type`, concrete targets or discovery location, real dependencies, and per-step proof.

## Author and submit

1. Ground outcome, current behavior, target files, and proof in repository evidence.
2. Write self-contained steps in Orient, Characterize, Change, Verify order.
3. Work steps include `Files`, concrete `Verify`, and observable `Expect`; `verify` and `discovery` include their required proof or location.
4. Submit with `ralph_submit_md_artifact` using `artifact_type: plan`. For a similar revision, use `ralph_edit_md_artifact`; it submits when valid.
5. Use `ralph_discard_md_draft` only for a genuine wholesale restart.

`schema_version` and `## Validation Overrides` are unsupported. Repair every diagnostic directly. The only step-less document is exactly `type: plan` plus `noop: true`.
