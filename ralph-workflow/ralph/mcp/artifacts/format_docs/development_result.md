# development_result artifact format

## What you are doing

You are reporting the outcome of a development task: what you did, what changed, and whether the work is done or only partially complete.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `development_result` and `content` set to a JSON string of your result payload.

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\"}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the task is fully done, or `"partial"` if more work is needed
- `summary` — a non-empty string describing what was done
- `files_changed` — a non-empty string listing the files that were modified (one per line with a dash prefix is a good format)

## Optional fields

- `next_steps` — required when `status` is `"partial"`; describes what still needs to be done
- `continuation` — required when `status` is `"partial"`; an object with `prior_session_id` (the current session id) so the next agent knows which session to continue from

## Complete example

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\"}"
}
```

## Common mistakes

- Do NOT set `status` to `"partial"` without also providing `next_steps` and `continuation` — both are required for partial results
- Do NOT leave `summary` empty — it must describe what was actually done
- Do NOT leave `files_changed` empty — list every file that was modified
- The `continuation` field must be an object like `{"prior_session_id": "<your-session-id>"}`, not just a string
- Do NOT use any status other than `"completed"` or `"partial"`
