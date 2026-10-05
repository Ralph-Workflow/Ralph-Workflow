---
name: submit-artifact
description: Use when submitting any Ralph Workflow artifact as a markdown document via ralph_submit_md_artifact, when pre-checking a draft with ralph_verify_md_artifact, or when a submission returned diagnostics with codes like MD001-MD007, SPEC001-SPEC012, or REF001-REF004 and you need the closed markdown grammar
version: 2.2.0
---

# submit-artifact

## Overview

Every Ralph Workflow artifact is one readable markdown document passed to the
tools as plain text. Plans are exempt from the shared grammar: frontmatter is
optional, as are headings, IDs, fields, dependencies, and work-unit structure.
Plan submission checks only readable, non-empty text with at least ten words,
no more than 4,000,000 raw UTF-8 bytes, and recognizable plan intent. Plans have
no structural errors or advisories; extraction is best effort and the planning
analyzer judges quality. Non-plan artifacts retain their per-type contracts.

Two MCP tools operate on every artifact type:

- `ralph_verify_md_artifact({"artifact_type": "<type>", "content": "<markdown>"})`
  — validate without persisting. Safe to call any number of times.
- `ralph_submit_md_artifact({"artifact_type": "<type>", "content": "<markdown>"})`
  — check and persist an accepted artifact atomically. Rejected documents
    receive no receipt; their staged draft remains available for revision.

Supported `artifact_type` values: `plan`, `development_result`,
`commit_message`, `commit_cleanup`, `fix_result`, `issues`,
`smoke_test_result`, `product_spec`, `planning_analysis_decision`,
`development_analysis_decision`, `review_analysis_decision`,
`policy_remediation_analysis_decision`.

For `plan`, `development_result`, `commit_message`, and `commit_cleanup`,
use the dedicated companion skills (`submit-plan-artifact`,
`submit-development-result-artifact`, `submit-commit-message-artifact`,
`submit-commit-cleanup-artifact`). This skill teaches the shared grammar
for non-plan artifacts; `submit-plan-artifact` supplies optional plan guidance.

## The Closed Grammar

These structural rules apply only to non-plan artifacts. Follow the exact
per-type contract; none of the rules below is a plan submission requirement:

1. Start with a frontmatter block: a `---` line, one `key: value` field per
   line, then a closing `---` line. Values are single-line and must not
   start or end with whitespace. Non-plan types require `type: <type>`.
2. After the frontmatter, use headings and sections documented for the
   artifact type. Most types use exact `## Section Name` headings.
3. Inside a section the grammar knows exactly four content shapes, and each
   type's spec decides which shapes a given section accepts:
   - Stable-ID list items: `- [ID] text` (or `- [ ] [ID] text` with a
     checkbox). The ID starts with a letter and uses only letters, digits,
     `_`, `-`. This is the default shape for most sections.
   - Indented continuation lines under a list item — per-item labeled
     fields such as `  Category: test` or `  Verify: pytest -q`.
   - Stable-ID sub-blocks: a `### [ID] Title` heading followed by body lines,
     where the type's spec supports blocks.
   - Plain body lines — prose and labeled lines, allowed where the type's
     spec supports body content.
4. IDs must satisfy the uniqueness scope documented by the artifact type.
5. Blank lines are ignored. Content in a shape the type does not accept is
   rejected. Unknown sections or frontmatter are rejected only for types whose
   format docs close those vocabularies.

Which sections a type requires, and what each item's text must contain, is
defined per type in `.agent/artifact-formats/<artifact_type>.md`.

## Core Flow

1. Read `.agent/artifact-formats/<artifact_type>.md` for the type's contract
   (or optional guidance for a plan).
2. Write the markdown document.
3. Optionally call `ralph_verify_md_artifact` to check it.
4. Call `ralph_submit_md_artifact` with the same `artifact_type` and
   `content`.

Minimal example (`fix_result`):

```markdown
---
type: fix_result
---

## Summary

- [SUM-1] Clamped the foo() index and re-ran the focused test suite green.

## Files Changed

- [F-1] src/foo.py
- [F-2] tests/test_foo.py
```

## Error Recovery

Both tools return `{"artifact_type", "valid", "diagnostics"}`. Each
diagnostic has `line`, `section`, `rule_id`, `message`, and `severity`.
Fix every `"severity": "error"` at the named line and resubmit. Warnings
describe accepted content that deserves attention; submitted values are not
silently rewritten. Plans never return structural diagnostics or advisories;
fix only the sanity-check issue, not the plan's shape. The structural codes
below describe non-plan contracts:

- `MD001`–`MD007` — grammar violations: bad heading shape, content outside
  a section, a list line missing its `[ID]`, non-list content in a
  list-only section, malformed or duplicate frontmatter field,
  unterminated frontmatter block.
- `SPEC001`–`SPEC012` — structure violations: character limit, missing/
  unknown/duplicate section or frontmatter field, empty or over-limit
  sections, canonical content validation failures (SPEC010 carries the
  exact message), list items in a block-only section (SPEC011), and a
  block-only section with no `### [ID] Title` block (SPEC012).
- `REF001`–`REF004` — reference violations: malformed ID, duplicate ID in
  a section, a reference to an unknown ID, or a dependency cycle.

An unknown `artifact_type` is rejected before parsing; use one of the
supported values listed above, spelled exactly.
