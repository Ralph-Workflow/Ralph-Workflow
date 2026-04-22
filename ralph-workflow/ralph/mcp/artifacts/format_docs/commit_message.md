# commit_message artifact format

## What you are doing

You are telling Ralph what commit message to use for the current changes.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `commit_message` and `content` set to a JSON string of your commit payload.

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

## Required fields (inside content)

- `type` — must be `"commit"` for a real commit, or `"skip"` to skip committing
- For `"commit"` type: `subject` — a one-line commit message following conventional commit format like `fix(auth): prevent token expiry race`
- For `"skip"` type: `reason` — a non-empty string explaining why no commit is needed

## Optional fields (for "commit" type only)

- `body` — a plain string with extra explanation (use this OR the detailed fields below, not both)
- `body_summary` — first paragraph of the commit body
- `body_details` — middle details paragraph
- `body_footer` — closing notes paragraph
- `files` — an array of file paths that were changed
- `excluded_files` — an array of objects, each with `path` (string) and `reason` (one of: `internal_ignore`, `not_task_related`, `sensitive`, `deferred`)

## Complete example

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

## Common mistakes

- Do NOT use a plain `"message"` key — that format is no longer accepted; use `"type"` and `"subject"` instead
- Do NOT use `"type": "fix"` — the `type` field must be `"commit"` or `"skip"`, not a conventional-commit prefix
- Do NOT write the conventional-commit prefix in `type` — the prefix goes in `subject` like `fix(scope): description`
- Do NOT use both `body` and `body_summary`/`body_details`/`body_footer` in the same payload
- The `subject` must follow conventional commit format: `<type>(<scope>): <description>` where `<type>` is one of feat, fix, docs, refactor, test, style, perf, build, ci, chore

## Dumb-proof checklist

- Did you set `artifact_type` to `"commit_message"`?
- Did you put `{"type": "commit", ...}` or `{"type": "skip", ...}` inside the content JSON string?
- Did you use `"subject"` (not `"message"`) for the commit message text?
- Did you spell the conventional commit prefix in `subject` like `fix(scope):` not just `fix:`?
- Did you stringify the content object into a JSON string for the `content` field?
