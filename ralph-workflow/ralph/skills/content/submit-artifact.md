---
name: submit-artifact
description: Use when submitting any Ralph Workflow artifact as a markdown document via ralph_submit_md_artifact, when pre-checking a draft with ralph_verify_md_artifact, or when a submission returned diagnostics with codes like MD001-MD008, SPEC001-SPEC010, or REF001-REF002 and you need the closed markdown grammar
version: 2.0.0
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
`development_analysis_decision`, `review_analysis_decision`.

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
   space). The plan companion skill documents its additional `### [S-n]` step
   blocks.
3. Inside a section, every content line is a stable-ID list item:
   `- [ID] text` (or `- [ ] [ID] text` with a checkbox). The ID starts with
   a letter and uses only letters, digits, `_`, `-`. Item text is one line.
4. IDs must be unique within their section.
5. Blank lines are ignored. Any other content — prose outside a list item,
   text before the first heading, unknown sections, unknown frontmatter
   fields — is rejected.

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
diagnostic has `line`, `section`, `code`, `message`, and `severity`.
Fix every `"severity": "error"` at the named line and resubmit. Warning
diagnostics identify accepted vocabulary choices for which the documented
default was applied.

- `MD001`–`MD008` — grammar violations: bad heading level, content outside
  a section, a list line missing its `[ID]`, malformed or unterminated
  frontmatter, duplicate frontmatter field.
- `SPEC001`–`SPEC010` — structure violations: missing/unknown/duplicate
  section or frontmatter field, section item-count limits, and canonical
  content validation failures (SPEC010 carries the exact message).
- `REF001`/`REF002` — malformed or duplicate stable ID in a section.

An unknown `artifact_type` is rejected before parsing; use one of the
supported values listed above, spelled exactly.
