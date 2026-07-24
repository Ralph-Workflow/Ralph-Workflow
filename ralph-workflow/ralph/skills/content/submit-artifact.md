---
name: submit-artifact
description: Use when submitting any Ralph Workflow artifact as a markdown document via ralph_submit_md_artifact, when pre-checking a draft with ralph_verify_md_artifact, or when a submission returned diagnostics with codes like MD001-MD007, SPEC001-SPEC012, or REF001-REF004 and you need the closed markdown grammar
version: 2.1.0
---

# submit-artifact

## Overview

Every Ralph Workflow artifact is one readable markdown document written in a
small, closed grammar and passed to the tools as plain text.

Two MCP tools operate on every artifact type:

- `ralph_verify_md_artifact({"artifact_type": "<type>", "content": "<markdown>"})`
  — validate without persisting. Safe to call any number of times.
- `ralph_submit_md_artifact({"artifact_type": "<type>", "content": "<markdown>"})`
  — validate and persist atomically. Rejected documents persist nothing.

Supported `artifact_type` values: `plan`, `development_result`,
`commit_message`, `commit_cleanup`, `fix_result`, `issues`,
`smoke_test_result`, `product_spec`, `planning_analysis_decision`,
`development_analysis_decision`, `review_analysis_decision`,
`policy_remediation_analysis_decision`.

For `plan`, `development_result`, `commit_message`, and `commit_cleanup`,
use the dedicated companion skills (`submit-plan-artifact`,
`submit-development-result-artifact`, `submit-commit-message-artifact`,
`submit-commit-cleanup-artifact`). This skill teaches the shared grammar
that every type builds on.

## The Closed Grammar

Follow these rules exactly; anything else is a diagnostic:

1. Start with a frontmatter block: a `---` line, one `key: value` field per
   line, then a closing `---` line. Values are single-line and must not
   start or end with whitespace. Every type requires at least `type: <type>`.
2. After the frontmatter, use `## Section Name` headings (exactly two `#`, one
   space).
3. Inside a section the grammar knows exactly four content shapes, and each
   type's spec decides which shapes a given section accepts:
   - Stable-ID list items: `- [ID] text` (or `- [ ] [ID] text` with a
     checkbox). The ID starts with a letter and uses only letters, digits,
     `_`, `-`. This is the default shape for most sections.
   - Indented continuation lines under a list item — per-item labeled
     fields such as `  Category: test` or `  Verify: pytest -q`.
   - Stable-ID sub-blocks: a `### [ID] Title` heading followed by free body
     lines (e.g. the plan's `### [S-n]` step blocks with `Type:`, `Files:`,
     `Depends on:`, `Satisfies:` lines).
   - Plain body lines — prose and labeled lines like `Intent:` or
     `Skills:`, allowed only where the type's spec says so (e.g. the plan's
     `## Summary` and `## Skills MCP` sections).
4. IDs must be unique within their section.
5. Blank lines are ignored. Content in a shape the section does not accept —
   prose in a list-only section, text before the first heading, unknown
   sections, unknown frontmatter fields — is rejected.

Which sections a type requires, and what each item's text must contain, is
defined per type in `.agent/artifact-formats/<artifact_type>.md`.

## Core Flow

1. Read `.agent/artifact-formats/<artifact_type>.md` for the type's
   frontmatter fields and sections.
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
Fix every `"severity": "error"` at the named line and resubmit. Warning
diagnostics identify accepted vocabulary choices for which the documented
default was applied.

- `MD001`–`MD007` — grammar violations: bad heading shape, content outside
  a section, a list line missing its `[ID]`, non-list content in a
  list-only section, malformed or duplicate frontmatter field,
  unterminated frontmatter block.
- `SPEC001`–`SPEC012` — structure violations: character limit, missing/
  unknown/duplicate section or frontmatter field, empty or over-limit
  sections, canonical content validation failures (SPEC010 carries the
  exact message), list items in a block-only section (SPEC011), and a
  block-only section with no `### [ID] Title` block (SPEC012). SPEC009 is
  a warning: an accepted vocabulary value was coerced to its documented
  default.
- `REF001`–`REF004` — reference violations: malformed ID, duplicate ID in
  a section, a reference to an unknown ID, or a dependency cycle.

An unknown `artifact_type` is rejected before parsing; use one of the
supported values listed above, spelled exactly.
