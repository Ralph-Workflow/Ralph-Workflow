---
name: submit-commit-message-artifact
description: Use when submitting a commit_message artifact with a structured commit or skip payload via ralph_submit_artifact, or when the conventional-commit subject regex was rejected and you need to recover the subject shape
---

# submit-commit-message-artifact

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical commit_message
format doc at `.agent/artifact-formats/commit_message.md`. Use it as a quick
lookup before submitting a commit_message artifact, not as a substitute for the
format doc. The format doc is the source of truth for every required field,
the dual body shape decision tree, and the type-closed enum.

**Skill name vs MCP tool name.** This skill is named
`submit-commit-message-artifact`. It is a separate name from the generic MCP
tool `ralph_submit_artifact`, which is the active submission entry point. Do
not conflate the two: the MCP tool for committing is `ralph_submit_artifact`
with `artifact_type="commit_message"`.

## When to Use

Use this skill when you are about to call `ralph_submit_artifact` to report
the commit message for the current pending diff and the message body shape is
not obvious from the diff alone. It is the right skill for the canonical
commit_message artifact only — for plans, development results, commit cleanup,
issues, fix_result, smoke_test_result, or analysis-decision artifacts, use
the companion `submit-artifact` skill instead.

If the diff is clearly a one-line typo or a single-file cosmetic change, you
do not need this skill; the body-less envelope in the format doc is enough.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/commit_message.md` once. It defines every
   required field, the dual body shape decision tree, the type closed enum,
   and the conventional-commit subject prefix invariant. Treat it as a
   contract you must match exactly.
2. Decide the artifact type:
   - `commit` — the diff should be committed with a conventional-commit subject.
   - `skip` — no commit is needed; a non-empty `reason` explains why.
3. Choose the body shape:
   - `body` (single string) when the explanation fits in 1-2 paragraphs.
   - `body_summary` + `body_details` + `body_footer` when the change is
     complex (multi-subsystem, refactor, migration, deprecation).
   - **Never both** — pick exactly one body shape or supply neither.
4. Optionally narrow the commit scope with `files` (array of strings) or
   `excluded_files` (array of `{path, reason}` objects with one of the four
   allowed reason values: `internal_ignore`, `not_task_related`, `sensitive`,
   `deferred`).
5. Build the inner payload as a plain JSON object (e.g.
   `{"type": "commit", "subject": "fix(auth): prevent token expiry race"}`).
6. Pass the inner payload as `content` either as the native JSON object
   or as a JSON-serialized string. Do NOT wrap it in an outer `{"type": ..., "content": ...}`
   envelope — Ralph Workflow adds artifact metadata itself.
7. Call
   `ralph_submit_artifact({"artifact_type": "commit_message", "content": {"type": "commit", "subject": "type(scope): description"}})`.
8. After the submit success text, call `ralph_declare_complete(summary="commit_message")`.

### Conventional-commit subject shape (the contract)

The `subject` field is matched by this regex:

```
^(feat|fix|docs|refactor|test|style|perf|build|ci|chore)(\([a-z0-9/_-]+\))?(!)?: [a-z0-9].+
```

Three accepted forms — pick exactly one per commit:

- `<type>: <description>` — no scope, no breaking marker.
  Example: `fix: clamp expiry window`.
- `<type>(<scope>): <description>` — scoped commit. `<scope>` must be a
  lowercase token of `[a-z0-9/_-]+` (letters, digits, `/`, `_`, `-`).
  Examples: `fix(auth): prevent token expiry race`,
  `feat(api/export): add CSV export`.
- `<type>(<scope>)!: <description>` or `<type>!: <description>` —
  **breaking change**. Append `!` immediately after the type or
  `type(scope)` token and BEFORE the colon. Examples:
  `feat(api)!: drop legacy /v1 endpoints`,
  `fix(parser)!: tighten whitespace handling`.

Rules that hold for every form:

- `<type>` must be one of the ten tokens above. No other tokens are
  accepted (`release`, `merge`, `wip`, etc. are rejected).
- `<scope>` is optional and must be lowercase `[a-z0-9/_-]+`. Multi-word
  scopes use `-` or `_` separators (`rate-limiter`, `csv_export`).
- A breaking change is marked with `!` immediately after the type or
  type(scope). The description must still start with a lowercase
  alphanumeric character, never with whitespace or punctuation.
- The `BREAKING CHANGE:` footer line is allowed but not required when
  `!` is present. If you only want to flag a breaking change in the
  body, do BOTH (the `!` AND the `BREAKING CHANGE:` footer) so the
  intent is unambiguous.
- A subject like `fix(auth) prevent token expiry race` (missing colon)
  or `Fix(auth): ...` (capitalized type) is rejected.

**Minimal one-shot happy-path envelope** for a commit with a body:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\", \"body\": \"Previously, concurrent refresh requests could cause the token to be invalidated while still being used by another request. This change serializes refresh operations per token to prevent that race condition.\"}"
}
```

**Minimal one-shot happy-path envelope** for a detailed-body commit:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"feat(api): add CSV export with filtered queries\", \"body_summary\": \"Adds support for exporting reports as CSV with client-side filtering.\", \"body_details\": \"The filter runs entirely in the browser, applying to the loaded dataset before export. This avoids the need for a new API endpoint while still giving users the filtering they expect.\", \"body_footer\": \"Fixes #42\"}"
}
```

**Minimal one-shot happy-path envelope** for a skip:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"skip\", \"reason\": \"No task-related changes since the last commit.\"}"
}
```

## Recovery from a Bad Payload

When `ralph_submit_artifact` rejects a `commit_message` payload, the helper
`_raise_format_doc_error` raises an `InvalidParamsError` whose message points
at `.agent/artifact-formats/commit_message.md` and names `ralph_submit_artifact`
as the retry tool. Read the message, then:

1. If the helper `_artifact_content_format_error` is raised, your payload is
   missing the `content` field. Re-issue the call with `content` set to a
   native JSON object or JSON-serialized string.
2. If validation complains about `type`, you supplied a conventional-commit
   prefix (`fix`, `feat`, etc.) or the legacy `message` key instead of the
   structured shape. The `type` field MUST be `"commit"` or `"skip"` — the
   conventional-commit prefix goes in `subject`, not `type`.
3. If validation complains about `body`, you supplied both `body` and the
   `body_summary` / `body_details` / `body_footer` triple. Pick exactly one
   shape and resubmit.
4. If validation complains about `excluded_files.reason`, your reason is not
   one of the four allowed values. Resubmit with `internal_ignore`,
   `not_task_related`, `sensitive`, or `deferred`.
5. If validation complains about `subject`, you forgot the conventional-commit
   prefix. The subject must look like `<type>(<scope>): <description>` (e.g.
   `fix(auth): clamp expiry window`), with `<type>` in
   `feat|fix|docs|refactor|test|style|perf|build|ci|chore` and a lowercase
   imperative description. For breaking changes, append `!` immediately
   after the type or `type(scope)` and BEFORE the colon — e.g.
   `feat(api)!: drop legacy /v1 endpoints` or
   `fix(parser)!: tighten whitespace handling`. Subjects without the
   breaking `!` are accepted for non-breaking changes; the `!` is the
   only marker that the runtime accepts in the subject (a `BREAKING
   CHANGE:` footer alone is informational, not a substitute).

**Worked retry envelope** for a `_raise_format_doc_error` style failure on
`commit_message`:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

**Worked retry envelope** for a `_artifact_content_format_error` style
failure (missing `content`):

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

## Source of Truth Reference

- `.agent/artifact-formats/commit_message.md` — the canonical schema for the
  commit_message artifact. Bundled by Ralph Workflow and materialized into the
  workspace on demand. Every required field, the dual body shape decision
  tree, and the `type` closed enum are defined here.
- `.agent/artifact-formats/artifact_formats_index.md` — the index that lists
  every supported `artifact_type` (including `commit_message`) and points to
  each format doc.

If this skill and the format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/commit_message.md` is the source of truth; this
  skill is a quick pointer, not a substitute.
- Conflating `submit-commit-message-artifact` (this skill) with the MCP
  tool `ralph_submit_artifact`. The MCP tool is the active submission entry
  point; the skill is the passive reference document.
- Supplying both `body` and `body_summary`/`body_details`/`body_footer` in
  the same payload. The two shapes are mutually exclusive — pick exactly
  one.
- Supplying `body` together with `body_summary` (and omitting
  `body_details`/`body_footer`) — the Pydantic model treats the triple as a
  group and rejects partial triples when `body` is also present. Either the
  full triple OR `body` alone, never a partial triple mixed with `body`.
- Writing the conventional-commit prefix in `type` (e.g.
  `{"type": "fix", ...}`). The `type` field must be `"commit"` or `"skip"`;
  the `fix:` prefix goes in `subject`.
- Supplying `"message"` instead of `"subject"`. The legacy `message` key is
  no longer accepted.
- Using `"type": "test"` or any other ad-hoc value for the `type` field. The
  closed set is `"commit"` and `"skip"`.
- Skipping the body for a non-trivial change. Most commits need a body — when
  in doubt, include one.
- Using `content_path` instead of `content`. Use `content` with a native JSON object
  or JSON-serialized string; `content_path` is reserved for non-agent callers.
- Treating a native JSON object as invalid. `content` may be a native JSON
  object or a JSON-serialized string; keep the outer
  `{"artifact_type": "commit_message", "content": ...}` envelope either way.
- Using `excluded_files[].reason` outside the four-value closed enum. The
  reason must be `internal_ignore`, `not_task_related`, `sensitive`, or
  `deferred`.
- Forgetting the `!` marker on a breaking change. Subjects like
  `feat(api): drop legacy /v1 endpoints` (no `!`) silently lose the
  breaking-change flag. Append `!` after the type or `type(scope)` and
  before the colon: `feat(api)!: drop legacy /v1 endpoints`.
- Misplacing the `!` marker. Only positions `type!:` and `type(scope)!:`
  are accepted. `type(scope)!extra:` or `type:!something` are rejected.
- Capitalizing the type token. `Fix(auth): ...` is rejected even though
  conventional-commit examples sometimes show `FIX(...)`. The runtime
  accepts only lowercase `<type>` from the ten-token set.
- Adding `!` for non-breaking changes. A breaking marker on a routine
  commit triggers SemVer-major bumps in downstream consumers and
  changelog tooling; reserve it for commits that actually break
  compatibility.

## Red Flags - STOP and Start Over

- "I have read the format doc so I do not need the skill." STOP. The
  skill is a per-tool retry envelope; the format doc is the schema. They
  cover different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL marker
  means the agent may consult the skill, not that the agent may skip the
  source-of-truth format doc. The skill names the format doc explicitly.
- "I will write the conventional-commit prefix in `type`." STOP. The
  `type` field must be `"commit"` or `"skip"`; the `fix:` prefix goes in
  `subject`.
- "I will skip the body for a non-trivial change." STOP. The commit
  message skill and the format doc both surface the dual body shape
  decision tree for a reason: most commits need a body — when in doubt,
  include one.
- "I will mark a routine commit as breaking with `!`." STOP. The `!`
  marker triggers SemVer-major bumps in downstream consumers; reserve
  it for commits that actually break compatibility.
- "I will mix `body` and `body_summary`/`body_details`/`body_footer`."
  STOP. The two shapes are mutually exclusive — pick exactly one.
