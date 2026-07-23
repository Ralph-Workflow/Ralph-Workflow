# fix_result artifact format

You are reporting the outcome of a fix task: what you fixed and what files
changed. Author markdown and submit with `ralph_submit_md_artifact`
(`artifact_type: fix_result`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/fix_result.md`

## Complete minimal example

```markdown
---
type: fix_result
---

## Summary

- [SUM-1] Applied reviewer fixes to the login validation path.

## Files Changed

- [F-1] src/main.py
- [F-2] tests/test_main.py
```

## Frontmatter

- `type` — required; `fix_result`. There is no status field.

## Sections

- `## Summary` — required; exactly one item describing what was fixed.
- `## Files Changed` — required; one item per modified file, at least one.
- `## Next Steps` — optional; at most one item for remaining follow-up.

## Hard errors vs warnings

Hard errors: missing Summary or Files Changed; more than one Summary or
Next Steps item; duplicate item IDs; any grammar violation (unknown
sections, stray prose lines). This type has no warning-level coercions.
