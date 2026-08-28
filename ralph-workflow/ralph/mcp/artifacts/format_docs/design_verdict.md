# design_verdict artifact format

You are reporting the outcome of a visual review of a captured
multimodal cell. Author markdown and submit with
`ralph_submit_md_artifact` (`artifact_type: design_verdict`).

See the complete sample artifact — valid format and a model of the craft:
`.agent/artifact-formats/examples/design_verdict.md`

A `design_verdict` is a closed review record: every finding cites a
specific captured cell, the verdict status matches the findings, and
the design intent is the verbatim text the agent was asked to review
so the result cannot be smuggled in by rephrasing the prompt into a
source-reading task.

## Complete minimal example

```markdown
---
type: design_verdict
judgement_tier: on-demand
---

## Capture Provenance

run_id: 2026-08-10-001
verdict_id: verdict-001
target: src/components/header.tsx
before_id: manifest-before-001
after_id: manifest-after-001
cell_ids: cap-001,cap-002,cap-003
before_handles: ralph://media/11111111-1111-1111-1111-111111111111
after_handles: ralph://media/22222222-2222-2222-2222-222222222222

## Design Intent

- [I-1] The header should align its three action buttons in a single row on desktop and stack them on mobile.

## Verdict

- [V-1] pass | Buttons align on desktop (cap-001), stack on mobile (cap-002), and the gap token matches the design system (cap-003).

## Findings

- [F-1] cap-001 | 0,0,320,40 | alignment | minor | The left and right button groups share the same vertical center but the action group is 2px off-grid at the smallest breakpoint.
- [F-2] cap-002 | 0,0,320,160 | stacking | info | The stack order is icon-then-label; the design system prefers label-then-icon for stacked action buttons.
```

## Frontmatter

- `type` — required; `design_verdict`. Any other value is a hard error.
- `judgement_tier` — required for visual proof; `deterministic` for fixture-only
  machinery tests or `on-demand` for the operator-invoked vision review.
- `status` — optional frontmatter echo; if present, must be `pass`,
  `fail`, or `blocked`. The authoritative status lives in
  `## Verdict`.

## Sections

- `## Capture Provenance` — required. The structural grammar requires
  `run_id:`, `target:`, `before_id:`, `after_id:`, and `cell_ids:`. For an
  active-run submission, also provide `judgement_tier` frontmatter plus
  `verdict_id:`, `before_handles:`, and `after_handles:`. `cell_ids` is a
  comma-separated list of capture identifiers; every `capture_id` cited in a
  finding must appear here. The handle fields are comma-separated server-minted
  `ralph://media/{artifact_id}` references for the compared sets.
- `## Design Intent` — required; exactly one item, the verbatim text
  the agent was asked to review.
- `## Verdict` — required; exactly one item shaped
  `status | summary`. `status` is `pass`, `fail`, or `blocked`.
- `## Findings` — required. Items shaped
  `capture_id | x,y,w,h | dimension | severity | narrative`.
  `region` is `x,y,w,h` with non-negative integers. `severity` is one
  of `blocker`, `major`, `minor`, or `info`.

Unknown descriptive frontmatter fields and sections are accepted and
ignored by the typed `design_verdict` consumer. Known `type`, `status`,
and the section shapes above remain strict.

## Hard errors vs warnings

Structural hard errors: missing `## Capture Provenance`, `## Design Intent`,
`## Verdict`, or `## Findings`; a required structural `Capture Provenance` body
field missing or empty; a `Design Intent` that smuggles a code-reading
phrase (`DOM`, `CSS`, `stylesheet`, `diff`, `classname`, the code-artifact
compounds `source code` / `class name` / `inline style` and their kin, or
`source` as the object of a directive such as `inspect the source`) --
matched whole-word and case-insensitively, so `different`, `kingdom`, and
`the visual style` are not rejected; a `Verdict` not shaped
`status | summary`; a `Findings` item not shaped
`capture_id | x,y,w,h | dimension | severity | narrative`; a finding
whose `capture_id` does not appear in `Capture Provenance` cell_ids; a
finding region that is not a non-negative `x,y,w,h`; a `Verdict` of
`pass` with at least one `blocker` or `major` finding; a `Verdict` of
`fail` with no `blocker` or `major` finding; an unknown frontmatter
`type` or `status`; or any malformed core grammar. Active-run submission adds
hard errors when `judgement_tier`, `verdict_id`, either compared handle set, or
ledger-backed active-run provenance is absent or invalid.
