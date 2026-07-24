# Artifact Formats Index

Ralph artifacts are single markdown documents with a small, closed grammar.
Write the document directly as readable frontmatter, sections, and stable-ID
items.

## How to submit

1. Write the markdown document for your artifact type (see its format doc).
2. Optionally call `ralph_verify_md_artifact` with `artifact_type` and
   `content` to lint it without submitting. It returns the same line-anchored
   diagnostics the submission gate uses.
3. Call `ralph_submit_md_artifact` with `artifact_type` and `content`.

For large plans you can stage incrementally: `ralph_stage_md_artifact` to
save a draft, `ralph_get_md_draft` to read it back, `ralph_discard_md_draft`
to drop it, and `ralph_finalize_md_artifact` to validate and submit the
staged document. To change one plan step without resubmitting the whole
document, use `ralph_edit_md_plan_step`.

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
- Every content line is one list item: `- [ID] text` (checkbox form
  `- [ ] [ID] text` is also accepted). Item text stays on one line.
- The plan format extends this grammar as documented in `plan.md`: `### [S-n]`
  step blocks with description prose, Summary prose, labeled field lines
  (`Intent:`, `Skills:`, `Directories:`, `Depends on:`, `Expect:`,
  `Satisfies:`, …), and indented per-item fields under list items.
- IDs match `[A-Za-z][A-Za-z0-9_-]*` and must be unique within a section.
- Blank lines are ignored. Anything else — stray prose, other heading
  levels, unknown sections or frontmatter fields — is an error.

## Errors vs warnings

Hard errors (submission rejected): malformed grammar, missing or unknown
frontmatter fields and sections, duplicate sections, missing required
sections or items, malformed or duplicate IDs, references to unknown IDs,
size caps, and each type's canonical content rules.

Warnings (accepted, with the documented default applied): unrecognized
vocabulary choices such as a status or intent label. Diagnostics carry
`line`, `section`, `rule_id`, `message`, and `severity`.

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
