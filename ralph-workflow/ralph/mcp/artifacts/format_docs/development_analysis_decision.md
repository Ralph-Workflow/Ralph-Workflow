# development_analysis_decision artifact format

## What you are doing

You are reporting the outcome of a development analysis review: whether the implementation is acceptable or needs changes, and exactly what to fix if changes are needed.

You can also submit this using `artifact_type: "analysis_decision"` when your session drain is `development_analysis`.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `development_analysis_decision` and `content` set to a JSON string of your decision payload.

```json
{
  "artifact_type": "development_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"Implementation looks correct.\", \"what_came_up_short\": [], \"how_to_fix\": []}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the implementation is acceptable, or `"request_changes"` if changes are needed
- `summary` — a non-empty string describing the overall analysis result
- `what_came_up_short` — an array of strings listing what is missing or wrong (can be empty if status is `"completed"`)
- `how_to_fix` — an array of strings with concrete steps to resolve any problems (can be empty if status is `"completed"`)

## Optional fields

There are no optional fields; all fields listed above are expected.

## Complete example

```json
{
  "artifact_type": "development_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"Implementation looks correct.\", \"what_came_up_short\": [], \"how_to_fix\": []}"
}
```

## Common mistakes

- Do NOT use any status other than `"completed"` or `"request_changes"`
- Do NOT leave `summary` empty — describe what the analysis found
- Do NOT submit a plain string as `content` — the content must be a JSON object
- Do NOT omit `what_came_up_short` or `how_to_fix` — include them as empty arrays `[]` if everything is fine
- Do NOT confuse this with `review_analysis_decision` — use this type for development analysis sessions, not review analysis sessions
