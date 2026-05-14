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

- `status` — either `"issues_found"` or `"no_issues"`
- `summary` — a non-empty string describing the overall review result

When `status` is `"issues_found"`, the following are also required:
- `issues` — a non-empty array of issue objects; each object must have:
  - `path` — the file path where the issue is found
  - `severity` — how bad the issue is (`"high"`, `"medium"`, or `"low"`)
  - `summary` — a short description of the specific problem
- `what_came_up_short` — a non-empty array of strings describing what is missing or wrong overall
- `how_to_fix` — a non-empty array of strings with concrete steps to resolve the problems

When `status` is `"no_issues"`, `issues`, `what_came_up_short`, and `how_to_fix` may be empty arrays.

## Optional fields

There are no optional fields; all fields listed above are expected.

## Complete example

```json
{
  "artifact_type": "issues",
  "content": "{\"status\": \"issues_found\", \"summary\": \"Found issues.\", \"issues\": [{\"path\": \"src/main.py\", \"severity\": \"high\", \"summary\": \"Missing validation\"}], \"what_came_up_short\": [\"No input validation\"], \"how_to_fix\": [\"Add validation\"]}"
}
```

## Clean review example

When the review passes without issues:

```json
{
  "artifact_type": "issues",
  "content": "{\"status\": \"no_issues\", \"summary\": \"The implementation is correct and all tests pass.\", \"issues\": [], \"what_came_up_short\": [], \"how_to_fix\": []}"
}
```

## Common mistakes

- Do NOT make `issues` a flat list of strings — each entry must be a JSON object with `path`, `severity`, and `summary`
- Do NOT leave `what_came_up_short` or `how_to_fix` as empty arrays if you found real problems
- Do NOT submit a plain string as `content` — the content must be a JSON object
- Do NOT use non-standard severity values; stick to `"high"`, `"medium"`, or `"low"`
- Do NOT omit `path` in an issue object — even if the issue is general, use the most relevant file path

## Dumb-proof checklist

- Did you set `artifact_type` to `"issues"`?
- Did you set `status` to `"issues_found"` or `"no_issues"`?
- Did you write a non-empty `summary`?
- If `status` is `"issues_found"`: is `issues` a non-empty array of objects?
- Does each issue object have `path`, `severity`, and `summary`?
- Is `severity` one of: `"high"`, `"medium"`, `"low"`?
- If `status` is `"issues_found"`: are `what_came_up_short` and `how_to_fix` non-empty?
- Did you stringify the content object into a JSON string for the `content` field?
