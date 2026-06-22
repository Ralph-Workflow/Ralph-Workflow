# planning_analysis_decision artifact format

## What you are doing

You are reporting the outcome of a planning analysis review: whether the proposed plan is sound and executor-ready or needs changes, and exactly what to fix if changes are needed.

You can also submit this using `artifact_type: "analysis_decision"` when your session drain is `planning_analysis`.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `planning_analysis_decision` and `content` set to a JSON string of your decision payload.

After a successful submit, the run-scoped artifact receipt is sufficient completion evidence for the current analysis flow. `declare_complete` remains an explicit signal but is not required just to make the submission count.

If your session drain is `planning_analysis`, the generic alias `artifact_type: "analysis_decision"` is also accepted. The payload shape is the same; only the outer `artifact_type` changes.

```json
{
  "artifact_type": "planning_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"The plan is executor-ready.\"}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the plan is sound and executor-ready, `"request_changes"` if the plan needs revision, or `"failed"` if the analysis result is unusable and the plan should be redone from the planning phase
- `summary` — a non-empty string describing the overall analysis result

## Optional fields (inside content)

- `what_came_up_short` — an array of strings listing what is missing or wrong (required when status is `"request_changes"` or `"failed"`, can be omitted when status is `"completed"`)
- `how_to_fix` — an array of strings with concrete steps to resolve any problems (required when status is `"request_changes"` or `"failed"`, can be omitted when status is `"completed"`)

## Complete example

```json
{
  "artifact_type": "planning_analysis_decision",
  "content": "{\"status\": \"completed\", \"summary\": \"The plan is executor-ready.\"}"
}
```

## Retry-ready non-completed example

```json
{
  "artifact_type": "planning_analysis_decision",
  "content": "{\"status\": \"request_changes\", \"summary\": \"The plan needs revision before execution.\", \"what_came_up_short\": [\"critical_files is missing the real target file\"], \"how_to_fix\": [\"Resubmit critical_files with primary_files populated, then rerun planning analysis\"]}"
}
```

## Alias example for `analysis_decision`

```json
{
  "artifact_type": "analysis_decision",
  "content": "{\"status\": \"request_changes\", \"summary\": \"The plan needs revision before execution.\", \"what_came_up_short\": [\"critical_files is missing the real target file\"], \"how_to_fix\": [\"Resubmit critical_files with primary_files populated, then rerun planning analysis\"]}"
}
```

## Common mistakes

- Do NOT use any status other than `"completed"`, `"request_changes"`, or `"failed"`
- Do NOT leave `summary` empty — describe what the analysis found
- Do NOT submit a plain string as `content` — the content must be a JSON object
- Do NOT omit `what_came_up_short` or `how_to_fix` when status is `"request_changes"` or `"failed"` — these fields are required
- Do NOT include `what_came_up_short` or `how_to_fix` when status is `"completed"` — these fields are not needed
- Do NOT confuse this with `development_analysis_decision` or `review_analysis_decision` — use this type for planning analysis sessions

## Dumb-proof checklist

- Did you set `artifact_type` to `"planning_analysis_decision"` or, when the drain is `planning_analysis`, the alias `"analysis_decision"`?
- Did you set `status` to `"completed"`, `"request_changes"`, or `"failed"` (not something else)?
- Did you write a non-empty `summary`?
- Did you omit `what_came_up_short` and `how_to_fix` when status is `"completed"`?
- Did you include `what_came_up_short` and `how_to_fix` when status is `"request_changes"` or `"failed"`?
- Did you stringify the content object into a JSON string for the `content` field?
