# Artifact Formats Index

## What you are doing

You are choosing which type of artifact to submit to Ralph. The artifact type tells Ralph what kind of work you are reporting or what decision you are making.

## How to submit

Pick the correct `artifact_type` for your case from the list below. Then call `ralph_submit_artifact` with BOTH:
- `artifact_type` set to the exact type name
- `content` set to a JSON string with the required fields for that type

Plan artifacts are the exception: use `ralph_submit_plan_section` or
`ralph_submit_plan_sections`, then `ralph_finalize_plan`.

### Examples

Submit a commit message:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

Submit a development result:

```json
{
  "artifact_type": "development_result",
  "content": "{\"status\": \"completed\", \"summary\": \"Implemented the feature.\", \"files_changed\": \"- src/main.py\", \"plan_items_proven\": [{\"plan_item\": \"Step 1: Add feature\", \"proof\": \"Added the feature and tests.\"}], \"analysis_items_addressed\": [{\"how_to_fix_item\": \"Add validation\", \"proof\": \"Added input validation.\"}]}"
}
```

Submit issues found during review:

```json
{
  "artifact_type": "issues",
  "content": "{\"status\": \"issues_found\", \"summary\": \"Found issues.\", \"issues\": [{\"path\": \"src/main.py\", \"severity\": \"high\", \"summary\": \"Missing validation\"}], \"what_came_up_short\": [\"No input validation\"], \"how_to_fix\": [\"Add validation\"]}"
}
```

## Required fields (for choosing artifact_type)

You must always provide:
- `artifact_type` — a string. Required. Must be one of the valid types listed below.

## Optional fields

This index does not define optional top-level MCP fields. Every `ralph_submit_artifact` call still requires both `artifact_type` and `content`. The per-type required fields live inside the `content` JSON. Read the specific format doc for your type.

## Complete example

This example shows the minimum fields needed to submit each type:

```json
{
  "artifact_type": "commit_message",
  "content": "{\"type\": \"commit\", \"subject\": \"fix(auth): prevent token expiry race\"}"
}
```

## Common mistakes

- Do NOT use `artifact_type` values that are not listed below — they will be rejected
- Do NOT leave out `artifact_type` — it is required
- Do NOT guess the artifact type — read the list below and pick the right one
- The generic alias `analysis_decision` is valid only inside an analysis drain. When you use that alias, read the matching drain-specific format doc (`development_analysis_decision.md`, `planning_analysis_decision.md`, or `review_analysis_decision.md`) and keep the same inner payload shape.

## Supported Artifact Types

| artifact_type | Purpose | Format doc path |
|--------------|---------|-----------------|
| `analysis_decision` | Generic alias for the matching `*_analysis_decision` type inside an analysis drain only | Read the matching drain-specific analysis decision format doc |
| `commit_message` | Submit a git commit message | `.agent/artifact-formats/commit_message.md` |
| `commit_cleanup` | Clean up files before committing | `.agent/artifact-formats/commit_cleanup.md` |
| `development_result` | Report the outcome of a development task | `.agent/artifact-formats/development_result.md` |
| `issues` | Report issues found during review | `.agent/artifact-formats/issues.md` |
| `fix_result` | Report the outcome of a fix task | `.agent/artifact-formats/fix_result.md` |
| `development_analysis_decision` | Report a development analysis decision | `.agent/artifact-formats/development_analysis_decision.md` |
| `planning_analysis_decision` | Report a planning analysis decision | `.agent/artifact-formats/planning_analysis_decision.md` |
| `review_analysis_decision` | Report a review analysis decision | `.agent/artifact-formats/review_analysis_decision.md` |
| `smoke_test_result` | Report the outcome of a manual runtime smoke test | `.agent/artifact-formats/smoke_test_result.md` |
| `product_spec` | Submit a product specification from the prompt-helper agent | `.agent/artifact-formats/product_spec.md` |
| `plan` | Submit a structured execution plan with `ralph_submit_plan_section` / `ralph_submit_plan_sections` and `ralph_finalize_plan` | `.agent/artifact-formats/plan.md` |

## Dumb-proof checklist

- Did you set `artifact_type` to an exact value from the table above?
- If you are in an analysis drain and intentionally used the `analysis_decision` alias, did you also read the matching drain-specific format doc before submitting?
- Did you spell `artifact_type` correctly (check the table for the exact spelling)?
- Did you put the required fields inside the `content` JSON string?
- Did you use the correct format doc for your artifact type?
- Did you stringify the content object into a JSON string for the `content` field?
- If you are submitting a plan, did you use the planning tools instead of the generic artifact tool?
