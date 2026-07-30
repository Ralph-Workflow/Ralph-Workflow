---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.1.0
---

# submit-plan-artifact

A plan is one Markdown document. Read `.agent/artifact-formats/plan.md` for
its optional structure. Everything else is a recommended authoring pattern, not required grammar.
`PLAN001` is the only blocking plan diagnostic: empty, too-short, or recognizably
non-plan text. Warnings and info are advice; record a reason under
`## Validation Overrides` when proceeding is the informed choice.

## Author and submit

1. Ground the outcome, current behavior, target files, risks, and proof in
   repository evidence.
2. Cover Orient, Characterize, Change, and Verify. Use a discovery step for an
   unknown rather than inventing a path or command.
3. Optionally check with `ralph_verify_md_artifact`, then submit with
   `ralph_submit_md_artifact` using `artifact_type: plan` and the full text.
4. For a similar revision, use `ralph_edit_md_artifact` on the staged draft;
   it submits when valid. Use `ralph_stage_md_artifact` with `replace_all` only
   for a wholesale rewrite. `ralph_get_md_draft` inspects the draft and
   `ralph_finalize_md_artifact` submits an assembled staged draft.
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

### [S-2] Change and prove the timeout behavior
Implement the smallest timeout change, then run the focused regression.

Type: verify
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0

## Verification
- [V-1] pytest tests/test_retry.py -q
  Expect: the focused retry tests pass with exit code 0
```
