# product_spec artifact format

You are turning the user's idea into a structured product specification.
Author markdown and submit with `ralph_submit_md_artifact`
(`artifact_type: product_spec`) after the user approves the draft.

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/product_spec.md`

## Complete minimal example

```markdown
---
type: product_spec
---

## Title

- [T-1] User Dashboard Redesign

## Scope

- [SC-1] Redesign the dashboard to improve task visibility for power users.

## Goals

- [G-1] Reduce time-to-action for common tasks from 5 clicks to 2.
- [G-2] Improve task status visibility at a glance.

## Users

- [U-1] Power users who perform daily tasks via the dashboard.

## Success Criteria

- [C-1] 90% of users complete core tasks in 2 clicks or fewer.
- [C-2] Dashboard loads in under 1.5 seconds on median hardware.
```

## Frontmatter

- `type` — required; `product_spec`.

## Sections

Required: `## Title` (exactly one item), `## Scope` (exactly one item),
`## Goals`, `## Users`, `## Success Criteria` (each at least one item).

Optional: `## Constraints`, `## Product Behavior`, `## UX UI Requirements`,
`## Scope Boundaries`, `## Open Questions` — one item per point.

Keep items distinct and scannable: 5-10 well-organized items per section
beat 30 loosely related ones. Prefix related items with a feature-area tag
(e.g. `[Dashboard] ...`) in large specs. Do not include implementation
details or code structure.

## Hard errors vs warnings

Hard errors: a missing required section; more than one Title or Scope
item; empty Goals, Users, or Success Criteria; wrong `type`; duplicate
item IDs; unknown sections or stray prose lines. This type has no
warning-level coercions.
