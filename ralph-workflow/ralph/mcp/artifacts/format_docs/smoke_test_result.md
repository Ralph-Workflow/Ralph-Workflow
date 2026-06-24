# smoke_test_result artifact format

## What you are doing

You are reporting the outcome of a manual runtime smoke test, including what Ralph observed to work and what Ralph observed to break.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `"smoke_test_result"` and `content` set to either a native JSON object or a JSON-serialized string containing the smoke test result payload.

```json
{
  "artifact_type": "smoke_test_result",
  "content": "{\"status\":\"passed\",\"summary\":\"Interactive Claude completed the smoke task and surfaced meaningful output.\",\"output_file\":\"tmp/interactive-claude-smoke/todo-list.js\",\"observed_working\":[\"tmp artifact created\",\"session id observed\",\"tool activity observed\"],\"observed_breaks\":[],\"headless_guide_checks\":[\"session capture\",\"tool activity\",\"completion signal\",\"parser events\",\"tmp artifact creation\"]}"
}
```

## Required fields

Inside the `content` payload you must provide:

- `status` — required string, one of `passed`, `failed`, or `partial`
- `summary` — required non-empty string summarizing the smoke outcome
- `output_file` — required non-empty string path to the smoke output file under `tmp/`
- `observed_working` — required array of strings describing what Ralph observed to work
- `observed_breaks` — required array of strings when `status` is `failed`; may be empty otherwise
- `headless_guide_checks` — required array of strings listing the semantic checks derived from the headless contract

## Optional fields

There are no additional optional fields in the normalized smoke test result payload.

## Complete example

```json
{
  "artifact_type": "smoke_test_result",
  "content": "{\"status\":\"passed\",\"summary\":\"Interactive Claude created the todo list, emitted semantic output, and submitted the smoke artifact.\",\"output_file\":\"tmp/interactive-claude-smoke/todo-list.js\",\"observed_working\":[\"tmp artifact created\",\"session id observed\",\"tool activity observed\",\"declare_complete observed\"],\"observed_breaks\":[],\"headless_guide_checks\":[\"session capture\",\"tool activity\",\"completion signal\",\"parser events\",\"tmp artifact creation\"]}"
}
```

## Common mistakes

- Do NOT write the smoke output outside `tmp/`
- Do NOT report guessed behavior — only record what Ralph actually observed
- Do NOT omit `headless_guide_checks`; the artifact must state which semantic checks were used
- Do NOT mark the result as `failed` without listing concrete `observed_breaks`
- Do NOT submit a plain non-JSON string; `content` must be a native JSON object or a JSON-serialized object

## Dumb-proof checklist

- Did you set `artifact_type` to `"smoke_test_result"`?
- Did you put the payload inside the `content` payload?
- Did you keep `output_file` under `tmp/`?
- Did you list concrete observed working signals?
- If the smoke failed, did you list concrete observed breaks?
- Did you include the headless-guide semantic checks Ralph compared against?
