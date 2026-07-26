# Artifact Formats Index

Ralph artifacts are single markdown documents with a small core grammar and
type-specific consumed fields. Write the document directly as readable
frontmatter, sections, and stable-ID items.

## How to submit

1. Write the markdown document for your artifact type (see its format doc).
2. Optionally call `ralph_verify_md_artifact` with `artifact_type` and
   `content` to lint it without submitting. It returns the same line-anchored
   diagnostics the submission gate uses.
3. Call `ralph_submit_md_artifact` with `artifact_type` and `content`.

For a similar revision, use `ralph_edit_md_artifact` to repair the staged
draft and submit it when valid. Stage incrementally with
`ralph_stage_md_artifact`, inspect with `ralph_get_md_draft`, and use
`ralph_finalize_md_artifact` to submit an assembled draft. Reserve
`ralph_discard_md_draft` or `mode="replace_all"` for a wholesale restart.

Every submission also stages its document as that artifact's draft, whether
or not it validated. To revise a submitted or rejected plan, edit that draft
in place with `ralph_edit_md_artifact` (`edits`: a list of `oldText`/`newText`
pairs, the same semantics as `edit_file`). That call is itself a submission —
it is `ralph_submit_md_artifact` starting from the existing draft, and it
persists the artifact canonically as soon as the edited draft validates, so
no separate `ralph_finalize_md_artifact` call is needed. A draft that still
has errors is kept for further repair and nothing is submitted; the response
reports `submitted` either way.

Resending the whole document with `ralph_stage_md_artifact`
(`mode="replace_all"`), or dropping it with `ralph_discard_md_draft`, is the
fallback for a wholesale rewrite only. Do not reach for either to recover
from validation errors or to make a revision whose content is substantially
similar to the draft — edit in place instead.

## Shared grammar

```markdown
---
type: <artifact_type>
key: value
---

## Section Name

- [ID-1] one line of text
- [ID-2] another line of text
```

- Frontmatter is a leading `---` block of single-line `key: value` fields.
  Values are taken literally and must be unquoted — quotes become part of
  the value.
- Section headings are `## Name` (two hashes, one space).
- Named sections commonly use list items shaped `- [ID] text` (checkbox form
  `- [ ] [ID] text` is also accepted). The per-type document says which known
  sections require items and which accept descriptive body prose.
- `plan.md` describes an optional planning shape. A plan remains valid unless
  it triggers that type's sole not-a-plan error (`PLAN001`).
- IDs match `[A-Za-z][A-Za-z0-9_-]*` and must be unique within each consumed
  section that validates list items.
- Blank lines are ignored. Content outside a section, malformed frontmatter,
  and unsupported heading shapes remain errors.
- Unknown descriptive frontmatter fields and sections are accepted. Typed
  consumers ignore those extensions; known consumed fields and sections
  remain subject to the exact per-type rules.

## Errors vs warnings

Each type defines its own errors and advisory findings. For `plan`, only
`PLAN001` rejects a submission; warnings and info remain visible but valid.
Diagnostics carry `line`, `section`, `rule_id`, `message`, and `severity`.

## Supported artifact types

| artifact_type | Purpose | Format doc path |
|--------------|---------|-----------------|
| `commit_message` | Git commit message (or skip) | `.agent/artifact-formats/commit_message.md` |
| `commit_cleanup` | Pre-commit file cleanup actions | `.agent/artifact-formats/commit_cleanup.md` |
| `development_result` | Outcome of a development task | `.agent/artifact-formats/development_result.md` |
| `issues` | Issues found during review | `.agent/artifact-formats/issues.md` |
| `fix_result` | Outcome of a fix task | `.agent/artifact-formats/fix_result.md` |
| `development_analysis_decision` | Development analysis decision | `.agent/artifact-formats/development_analysis_decision.md` |
| `planning_analysis_decision` | Planning analysis decision | `.agent/artifact-formats/planning_analysis_decision.md` |
| `review_analysis_decision` | Review analysis decision | `.agent/artifact-formats/review_analysis_decision.md` |
| `policy_remediation_analysis_decision` | Project-policy remediation analysis decision | `.agent/artifact-formats/policy_remediation_analysis_decision.md` |
| `smoke_test_result` | Manual runtime smoke-test outcome | `.agent/artifact-formats/smoke_test_result.md` |
| `product_spec` | Product specification | `.agent/artifact-formats/product_spec.md` |
| `plan` | Structured execution plan | `.agent/artifact-formats/plan.md` |

Use the exact `artifact_type` string from the table and set the same value
in the document's `type:` frontmatter field.

## Sample artifacts

Every type above ships a complete sample artifact at
`.agent/artifact-formats/examples/<artifact_type>.md`. Each sample passes
`ralph_verify_md_artifact` as-is and models the craft (a well-structured
plan, a model conventional commit, honest proof discipline). Read the
sample for your type before authoring.
