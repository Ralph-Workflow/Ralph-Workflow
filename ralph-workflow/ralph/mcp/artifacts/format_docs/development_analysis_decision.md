# development_analysis_decision artifact format

## What you are doing

You are reporting the outcome of a development analysis review: whether the implementation is acceptable or needs changes, and exactly what to fix if changes are needed.

You can also submit this using `artifact_type: "analysis_decision"` when your session drain is `development_analysis`.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `development_analysis_decision` and `content` set to either a native JSON object or a JSON-serialized string containing your decision payload.

After a successful submit, the run-scoped artifact receipt is sufficient completion evidence for the current analysis flow. `declare_complete` remains an explicit signal but is not required just to make the submission count.

If your session drain is `development_analysis`, the generic alias `artifact_type: "analysis_decision"` is also accepted. The payload shape is the same; only the outer `artifact_type` changes.

```json
{
  "artifact_type": "development_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"Implementation looks correct.\"}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the implementation is acceptable, `"request_changes"` if changes are needed, or `"failed"` if the analysis result is unusable and the implementation should be redone from the work phase
- `summary` — a non-empty string describing the overall analysis result

## Optional fields (inside content)

- `what_came_up_short` — an array of strings listing what is missing or wrong (required when status is `"request_changes"` or `"failed"`, can be omitted when status is `"completed"`)
- `how_to_fix` — an array of strings with concrete steps to resolve any problems (required when status is `"request_changes"` or `"failed"`, can be omitted when status is `"completed"`)

## Complete example

```json
{
  "artifact_type": "development_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"Implementation looks correct.\"}"
}
```

## Retry-ready non-completed example

```json
{
  "artifact_type": "development_analysis_decision",
  "content": "{\"status\": \"request_changes\", \"summary\": \"The implementation still needs revision.\", \"what_came_up_short\": [\"verification_strategy omitted the exact test command\"], \"how_to_fix\": [\"Add the exact pytest target and rerun development analysis\"]}"
}
```

## Alias example for `analysis_decision`

```json
{
  "artifact_type": "analysis_decision",
  "content": "{\"status\": \"request_changes\", \"summary\": \"The implementation still needs revision.\", \"what_came_up_short\": [\"verification_strategy omitted the exact test command\"], \"how_to_fix\": [\"Add the exact pytest target and rerun development analysis\"]}"
}
```

## Common mistakes

- Do NOT use any status other than `"completed"`, `"request_changes"`, or `"failed"`
- Do NOT leave `summary` empty — describe what the analysis found
- Do NOT submit a plain non-JSON string as `content` — use a native JSON object or a JSON-serialized object
- Do NOT omit `what_came_up_short` or `how_to_fix` when status is `"request_changes"` or `"failed"` — these fields are required
- Do NOT include `what_came_up_short` or `how_to_fix` when status is `"completed"` — these fields are not needed
- Do NOT confuse this with `review_analysis_decision` — use this type for development analysis sessions, not review analysis sessions

## Dumb-proof checklist

- Did you set `artifact_type` to `"development_analysis_decision"` or, when the drain is `development_analysis`, the alias `"analysis_decision"`?
- Did you set `status` to `"completed"`, `"request_changes"`, or `"failed"` (not something else)?
- Did you write a non-empty `summary`?
- Did you omit `what_came_up_short` and `how_to_fix` when status is `"completed"`?
- Did you include `what_came_up_short` and `how_to_fix` when status is `"request_changes"` or `"failed"`?
- Did you provide `content` as either a native JSON object/array or a JSON-serialized string?
