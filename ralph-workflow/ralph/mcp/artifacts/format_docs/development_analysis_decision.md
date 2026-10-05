# development_analysis_decision artifact format

Report whether each criterion fixed by the request and plan is met. Submit
markdown with `ralph_submit_md_artifact`
(`artifact_type: development_analysis_decision`).

## Completed example

```markdown
---
type: development_analysis_decision
status: completed
---

## Summary

- [SUM-1] No counterexample was found for the fixed criteria.

## Criterion Verdicts

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: met. Evidence: `pytest tests/test_feature.py -q` reports 12 passed. Location: tests/test_feature.py:42.
```

## Request-changes example

```markdown
---
type: development_analysis_decision
status: request_changes
---

## Summary

- [SUM-1] One fixed criterion is not met.

## What Came Up Short

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: not met. Evidence: `pytest tests/test_foo.py -q` has no oversized-index case. Location: tests/test_foo.py. Remaining work: tests/test_foo.py still lacks an oversized-index case, so the overflow path is unproven.

## Criterion Verdicts

- [DA-001] Criterion: oversized indexes are handled safely. Expected observation: the focused test exercises an oversized index. Verdict: not met. Evidence: `pytest tests/test_foo.py -q` has no oversized-index case. Location: tests/test_foo.py.
```

## Sections

- `## Summary` is required and has exactly one item.
- `## Criterion Verdicts` is required and non-empty for every decision. Each
  item has a unique `DA-###` ID and `Criterion:`, `Expected observation:`,
  `Verdict:`, non-empty `Evidence:`, and non-empty `Location:` fields. Every
  non-met verdict has a same-ID mirror in `## What Came Up Short`.
- `## What Came Up Short` is required and non-empty for `request_changes` and
  `failed`; it mirrors localized non-met criterion verdicts and is omitted for
  `completed`. For `request_changes`, every finding must independently include
  a non-empty `Remaining work:` statement describing the leftover development
  work (state what is still missing or unproven — the development phase owns
  how to fix it), a concrete repository `Location:` (not `unknown`/`N/A`/`none`), and
  identify `Criterion:` or a `Plan reference: [<stable id>]` the plan uses. A
  single well-formed finding
  does not excuse a sibling that lacks any of the three.
- Whole-change findings reuse the same `DA-###` shape and the same
  `## Criterion Verdicts` block. There is no separate integration section;
  emit a `DA-###` item when a problem only shows up when the change is
  reviewed as a whole (parallel pieces that do not fit together, regressions
  outside the plan, unrelated scope drift, or a `AGENTS.md` /
  `docs/ralph-workflow-policy/` violation). Non-met whole-change findings
  still mirror into `## What Came Up Short` with the same `Remaining work:`
  contract.
- `## How To Fix` is not permitted. `## Analysis Items Addressed` cites the
  stable finding ID as its closure reference, not a remedy authored by the
  verifier.
- The bundled validator lives at
  `ralph/mcp/artifacts/markdown/specs/analysis_decision.py` and is
  registered via `ANALYSIS_DECISION_SPECS`; every diagnostic rule this doc
  describes (`ANALYSIS002`–`ANALYSIS019`) is emitted from that module. The
  shared spec is the source of truth — if this format doc and the spec ever
  drift, the spec wins.

## Plan reference coverage

Besides the per-criterion items, emit one additional `DA-###` item for
whatever references the plan actually uses. These items are required, not
optional: the development-result proof gate no longer matches plan IDs,
so this decision is where plan coverage is recorded. Write whatever
stable label the plan itself uses inside the brackets — a numeric `S-n`
ID, a named anchor, or, for a prose plan, the prose anchor being judged —
because a prose plan is judged on whether its requests are covered, not
on whether a specific identifier matches. The shared validator's subject
slot already accepts any free-form `Plan reference: [<stable id>]` — see
`ralph/mcp/artifacts/markdown/specs/analysis_decision.py` at
`_PLAN_REFERENCE_PATTERN` (line 82) and `_finding_fields_complete` (line
49).

A plan-reference item keeps the standard
`Criterion:`/`Expected observation:`/`Verdict:`/`Evidence:`/`Location:`
shape — the validator requires those fields on every
`## Criterion Verdicts` item — with `Criterion:` stating the plan
reference's expected outcome, and adds `Plan reference: [<stable id>]`
paired with `Disposition: completed|adapted|not_applicable|blocked` in
the same item. `Plan reference:` and `Disposition:` are mandatory on
plan-reference items only: ordinary request-criterion findings (e.g.
`Criterion: oversized indexes are handled safely.`) are not plan
references and do not invent the pair. The bundled example models this
split: DA-003 is the request-criterion finding and DA-004 is the
separate plan-reference item carrying `Plan reference: [WU-1]` with
`Disposition: blocked`, mirrored consistently in `## What Came Up Short`
and `## Criterion Verdicts`.

`status` is `completed`, `request_changes`, or `failed`. `met` means no
counterexample was found. `not evaluable` requires `failed` rather than
completion.

`request_changes` means localized unmet work is actionable inside the current
development cycle. `failed` records stronger or not-evaluable evidence, such as
an impossible or contradictory criterion. Bundled policy routes both statuses
back to development; a failed decision adds an explicit mandate to determine
whether and how the failure can be resolved, without treating the label itself
as a pipeline failure. Terminal failure remains reserved for real pipeline or
recovery exhaustion.

See `.agent/artifact-formats/examples/development_analysis_decision.md` for the
validator-backed complete example.
