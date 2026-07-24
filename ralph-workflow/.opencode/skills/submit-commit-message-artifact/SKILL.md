---
name: submit-commit-message-artifact
description: Use when submitting a commit_message artifact as markdown via ralph_submit_md_artifact with a commit or skip frontmatter variant, or when the conventional-commit subject was rejected and you need the exact subject rule
version: 2.0.0
---

# submit-commit-message-artifact

## Overview

A commit message is one markdown document
(`artifact_type: "commit_message"`) with two frontmatter variants: a
`commit` variant carrying the subject and optional body sections, and a
`skip` variant carrying a reason not to commit.

Submit with `ralph_submit_md_artifact`; pre-check with
`ralph_verify_md_artifact`.

## Document Shape

**Commit variant** — frontmatter `type: commit` and `subject: <subject>`.
Optional sections (each item is `- [ID] text` on one line):

| Section | Items | Meaning |
|---|---|---|
| `## Body` | exactly 1 | free-form body paragraph |
| `## Body Summary` / `## Body Details` / `## Body Footer` | exactly 1 each | structured body; use these OR `## Body`, never both |
| `## Files` | 1+ | files to include in the commit |
| `## Excluded Files` | 1+ | `<path> \| <reason>` per item |

Each `## Excluded Files` reason must be one of `internal_ignore`,
`not_task_related`, `sensitive`, `deferred`.

**Skip variant** — frontmatter `type: skip` and `reason: <non-empty
reason>`. No sections.

## Subject Rule (strict)

The subject must match the conventional-commit pattern exactly:

```
^(feat|fix|docs|refactor|test|style|perf|build|ci|chore)(\([a-z0-9/_-]+\))?(!)?: [a-z0-9].+
```

In practice:

- Start with one of the ten types, lowercase.
- Optional scope in parentheses: lowercase letters, digits, `/`, `_`, `-`.
- Optional `!` before the colon marks a breaking change: `feat!:` or
  `feat(api)!:`.
- Then `: ` and a description starting with a lowercase letter or digit.

Valid: `fix(parser): preserve prefixed transcript lines`,
`feat(api)!: drop the v1 endpoints`. Invalid: `Fix: bug` (uppercase type),
`fix: Bug` (uppercase description start), `update stuff` (no type).

## Core Flow

1. Pick the variant: `commit` when there is a diff worth committing,
   `skip` (with a reason) when there is not.
2. Write the document and submit it with
   `ralph_submit_md_artifact({"artifact_type": "commit_message", "content": ...})`.

Worked commit example:

```markdown
---
type: commit
subject: fix(auth): prevent token expiry race
---

## Body

- [B-1] Concurrent refresh requests could invalidate a token while another request was still using it. Refresh operations are now serialized per token.
```

Worked skip example:

```markdown
---
type: skip
reason: only generated artifacts changed; nothing user-facing to commit
---
```

## Error Recovery

- `commit_message subjects must use conventional commit format ...` —
  rewrite the `subject:` frontmatter value against the Subject Rule
  above; check the type spelling, lowercase description start, and `: `
  separator.
- `frontmatter 'type' must be 'commit' or 'skip'` — no other values.
- `Use either 'body' or the detailed body fields, not both` — remove
  `## Body` or remove the `## Body Summary`/`## Body Details`/
  `## Body Footer` sections.
- A skip without a reason is rejected — `reason:` must be non-empty.
- An empty optional section (heading present, no item) is rejected —
  either give the section exactly one item or delete the heading.
