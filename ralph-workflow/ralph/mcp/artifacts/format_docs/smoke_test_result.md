# smoke_test_result artifact format

You are reporting the outcome of a manual runtime smoke test: what was
observed to work and what broke. Author markdown and submit with
`ralph_submit_md_artifact` (`artifact_type: smoke_test_result`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/smoke_test_result.md`

## Complete minimal example

```markdown
---
type: smoke_test_result
status: passed
output_file: tmp/interactive-claude-smoke/todo-list.js
---

## Summary

- [SUM-1] The smoke task completed and produced meaningful output.

## Observed Working

- [OK-1] tmp artifact created
- [OK-2] session id observed

## Headless Guide Checks

- [HG-1] session capture
- [HG-2] tool activity
- [HG-3] completion signal
```

## Frontmatter

- `type` — required; `smoke_test_result`.
- `status` — required; `passed`, `failed`, or `partial`. An unknown value
  is coerced to `partial` with a warning.
- `output_file` — required; path to the smoke output file (keep it under
  `tmp/`).

## Sections

- `## Summary` — required; exactly one item.
- `## Observed Working` — optional; one item per signal actually observed.
- `## Observed Breaks` — one item per concrete break; required (non-empty)
  when `status: failed`.
- `## Headless Guide Checks` — required; at least one item naming each
  semantic check compared against the headless contract.

Record only what was actually observed — never guessed behavior.

## Hard errors vs warnings

Hard errors: missing `output_file`; missing or multiple Summary items;
empty or missing Headless Guide Checks; `failed` without Observed Breaks;
wrong `type`; duplicate item IDs; any grammar violation. Warning: unknown
`status` coerced to `partial`.
