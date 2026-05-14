# commit_message artifact format

## What you are doing

You are telling Ralph what commit message to use for the current changes.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `"commit_message"` and `content` set to a JSON string of your commit payload.

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\", \"body\": \"Previously, concurrent refresh requests could cause the token to be invalidated while still being used by another request. This change serializes refresh operations per token to prevent that race condition.\"}"
}
```

## Required fields (inside content)

- `type` — must be `"commit"` for a real commit, or `"skip"` to skip committing
- For `"commit"` type: `subject` — a one-line commit message following conventional commit format like `fix(auth): prevent token expiry race`
- For `"skip"` type: `reason` — a non-empty string explaining why no commit is needed

## When to include a body

**Most commits should include a body.** The subject alone cannot convey rationale, impact, or context.

**A body is required when:**
- The diff touches multiple files or affects multiple subsystems
- The change adds, removes, or modifies behavior
- A refactor changes public APIs, data structures, or contracts
- Migration notes, deprecation warnings, or compatibility concerns apply
- The *why* is not immediately obvious from reading the subject

**One-liners (no body) are only acceptable for:**
- Trivial typo fixes or single-character corrections
- Single-file cosmetic changes with obvious intent
- Truly obvious micro-changes where the subject fully captures everything

## Body structure: simple vs detailed

**Use `body` (single string) when:**
- The explanation is concise and self-contained (1-2 paragraphs)
- The rationale is straightforward and doesn't need separation

**Use detailed body fields for complex changes:**
- `body_summary`: What changed and why in 1-2 sentences
- `body_details`: Deep dive — edge cases handled, design decisions, alternatives considered, behavioral notes
- `body_footer`: Breaking changes, migration steps, deprecation warnings, "Fixes #123", "Closes #456"

Complex refactors, architecture changes, new features with non-obvious behavior, and changes affecting multiple subsystems should use the detailed structure.

## Optional fields (for "commit" type only)

- `body` — a plain string with extra explanation (use this OR the detailed fields below, not both)
- `body_summary` — first paragraph of the commit body
- `body_details` — middle details paragraph
- `body_footer` — closing notes paragraph (e.g., "Fixes #123")
- `files` — an array of file paths that were changed
- `excluded_files` — an array of objects, each with `path` (string) and `reason` (one of: `internal_ignore`, `not_task_related`, `sensitive`, `deferred`)

## Complete example with body

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\", \"body\": \"Previously, concurrent refresh requests could cause the token to be invalidated while still being used by another request. This change serializes refresh operations per token to prevent that race condition.\"}"
}
```

## Complete example with detailed body fields

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"feat(api): add CSV export with filtered queries\", \"body_summary\": \"Adds support for exporting reports as CSV with client-side filtering.\", \"body_details\": \"The filter runs entirely in the browser, applying to the loaded dataset before export. This avoids the need for a new API endpoint while still giving users the filtering they expect.\", \"body_footer\": \"Fixes #42\"}"
}
```

## Common mistakes

- Do NOT use a plain `"message"` key — that format is no longer accepted; use `"type"` and `"subject"` instead
- Do NOT use `"type": "fix"` — the `type` field must be `"commit"` or `"skip"`, not a conventional-commit prefix
- Do NOT write the conventional-commit prefix in `type` — the prefix goes in `subject` like `fix(scope): description`
- Do NOT use both `body` and `body_summary`/`body_details`/`body_footer` in the same payload
- Do NOT assume a one-liner is sufficient for complex changes — when in doubt, add a body
- The `subject` must follow conventional commit format: `<type>(<scope>): <description>`

## Dumb-proof checklist

- Did you set `artifact_type` to `"commit_message"`?
- Did you put `{"type": "commit", ...}` or `{"type": "skip", ...}` inside the content JSON string?
- Did you use `"subject"` (not `"message"`) for the commit message text?
- Did you spell the conventional commit prefix in `subject` like `fix(scope):` not just `fix:`?
- Did you stringify the content object into a JSON string for the `content` field?
- If this is a non-trivial change, did you include a body?
