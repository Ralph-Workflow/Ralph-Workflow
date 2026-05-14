# fix_result artifact format

## What you are doing

You are reporting the outcome of a fix task: what you fixed and what files changed.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `fix_result` and `content` set to a JSON string of your fix result payload.

```json
{
  "artifact_type": "fix_result",
  "content": "{\"summary\": \"Applied reviewer fixes.\", \"files_changed\": \"- src/main.py\"}"
}
```

## Required fields (inside content)

- `summary` — a non-empty string describing what was fixed
- `files_changed` — a non-empty string listing the files that were modified (one per line with a dash prefix is a good format)

## Optional fields

- `next_steps` — an optional string describing any remaining work or follow-up actions

## Complete example

```json
{
  "artifact_type": "fix_result",
  "content": "{\"summary\": \"Applied reviewer fixes.\", \"files_changed\": \"- src/main.py\"}"
}
```

## Common mistakes

- Do NOT leave `summary` empty — describe what was actually fixed
- Do NOT leave `files_changed` empty — list every file that was modified
- Do NOT submit a plain string as `content` — the content must be a JSON object
- Do NOT add extra fields that are not listed above
- Do NOT use `status` — fix_result does not have a status field; just describe the work in `summary`

## Dumb-proof checklist

- Did you set `artifact_type` to `"fix_result"`?
- Did you write a non-empty `summary` describing what was fixed?
- Did you write a non-empty `files_changed` listing every file you modified?
- Did you NOT add a `status` field (fix_result does not use it)?
- Did you stringify the content object into a JSON string for the `content` field?
