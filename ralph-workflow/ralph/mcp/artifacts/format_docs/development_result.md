# development_result artifact format

## What you are doing

You are reporting the outcome of a development task: what you did, what changed, and whether the work is done or only partially complete.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `development_result` and `content` set to a JSON string of your result payload.

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\", \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add validation\", \"proof\": \"Updated src/main.py and tests.\"}], \"analysis_items_addressed\": [{\"how_to_fix_item\": \"Add test for edge case\", \"proof\": \"Added the regression test and verified it passes.\"}]}"
}
```

## Required fields (inside content)

- `status` — must be `"completed"` if the task is fully done, or `"partial"` if more work is needed
- `summary` — a non-empty string describing what was done
- `files_changed` — a non-empty string listing the files that were modified (one per line with a dash prefix is a good format)

## Proof fields (policy-required)

- `plan_items_proven` — array of `{plan_item: str, proof: str}` objects
  - For steps plans, include one entry per plan step
  - `plan_item` must exactly match the canonical step reference `"Step N: <title>"`
  - For work_units plans, include one entry for your assigned work unit and `plan_item` must exactly match that work unit's `unit_id`
  - Unknown references and duplicate `plan_item` values fail validation
- `analysis_items_addressed` — array of `{how_to_fix_item: str, proof: str}` objects
  - Include one entry per prior `how_to_fix` item when analysis feedback exists
  - `how_to_fix_item` must be an exact verbatim copy of the prior analysis text
  - Unknown references and duplicate `how_to_fix_item` values fail validation

## Optional fields

- `next_steps` — required when `status` is `"partial"`; describes what still needs to be done
- `continuation` — required when `status` is `"partial"`; an object with `prior_session_id` (the current session id) so the next agent knows which session to continue from

## Complete example

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\", \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add validation\", \"proof\": \"Updated src/main.py and tests.\"}], \"analysis_items_addressed\": [{\"how_to_fix_item\": \"Add test for edge case\", \"proof\": \"Added the regression test and verified it passes.\"}]}"
}
```

## Common mistakes

- Do NOT set `status` to `"partial"` without also providing `next_steps` and `continuation` — both are required for partial results
- Do NOT leave `summary` empty — it must describe what was actually done
- Do NOT leave `files_changed` empty — list every file that was modified
- Do NOT paraphrase `plan_item` references; use the exact canonical `"Step N: <title>"` form or the exact assigned `unit_id`
- Do NOT omit required proof entries when proof policy is enabled
- Do NOT add extra proof entries that do not correspond to a real plan step, work unit id, or analysis item
- The `continuation` field must be an object like `{"prior_session_id": "<your-session-id>"}`, not just a string
- Do NOT use any status other than `"completed"` or `"partial"`

## Dumb-proof checklist

- Did you set `artifact_type` to `"development_result"`?
- Did you set `status` to either `"completed"` or `"partial"` (not something else)?
- Did you write a non-empty `summary` describing what you did?
- Did you write a non-empty `files_changed` listing every file you modified?
- If proof policy is enabled, did you add the required `plan_items_proven` and `analysis_items_addressed` entries?
- Do the `plan_item` values exactly match canonical refs or assigned work unit ids?
- Do the `how_to_fix_item` values exactly copy the prior analysis text?
- If `status` is `"partial"`, did you also include `next_steps` and `continuation`?
- Did you stringify the content object into a JSON string for the `content` field?
