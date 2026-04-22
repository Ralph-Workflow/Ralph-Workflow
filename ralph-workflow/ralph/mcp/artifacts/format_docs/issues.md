# issues artifact format

## What you are doing

You are reporting a list of issues found during a review, explaining what is wrong, where it is, and how to fix it.

## How to submit

Call the `ralph_submit_artifact` tool with `artifact_type` set to `issues` and `content` set to a JSON string of your issues payload.

```json
{
  "artifact_type": "issues",
  "content": "{\"status\": \"issues_found\", \"summary\": \"Found issues.\", \"issues\": [{\"path\": \"src/main.py\", \"severity\": \"high\", \"summary\": \"Missing validation\"}], \"what_came_up_short\": [\"No input validation\"], \"how_to_fix\": [\"Add validation\"]}"
}
```

## Required fields (inside content)

- `status` — a string describing the overall outcome, such as `"issues_found"` or `"no_issues"`
- `summary` — a non-empty string describing the overall review result
- `issues` — an array of issue objects; each object must have:
  - `path` — the file path where the issue is found
  - `severity` — how bad the issue is (e.g. `"high"`, `"medium"`, `"low"`)
  - `summary` — a short description of the specific problem
- `what_came_up_short` — an array of strings describing what is missing or wrong overall
- `how_to_fix` — an array of strings with concrete steps to resolve the problems

## Optional fields

There are no optional fields; all fields listed above are expected.

## Complete example

```json
{
  "artifact_type": "issues",
  "content": "{\"status\": \"issues_found\", \"summary\": \"Found issues.\", \"issues\": [{\"path\": \"src/main.py\", \"severity\": \"high\", \"summary\": \"Missing validation\"}], \"what_came_up_short\": [\"No input validation\"], \"how_to_fix\": [\"Add validation\"]}"
}
```

## Common mistakes

- Do NOT make `issues` a flat list of strings — each entry must be a JSON object with `path`, `severity`, and `summary`
- Do NOT leave `what_came_up_short` or `how_to_fix` as empty arrays if you found real problems
- Do NOT submit a plain string as `content` — the content must be a JSON object
- Do NOT use non-standard severity values; stick to `"high"`, `"medium"`, or `"low"`
- Do NOT omit `path` in an issue object — even if the issue is general, use the most relevant file path
