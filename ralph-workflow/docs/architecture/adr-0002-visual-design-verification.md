# ADR-0002: Visual design verification — renderer, capture, verdict, ledger

- **Status:** Accepted
- **Date:** 2026-08-10
- **Supersedes:** None
- **Scope:** Maintained Python package (`ralph-workflow/ralph/`)

## Context

`PROMPT.md` and `.agent/PRODUCT_CRITERIA.md` describe eighteen
binding criteria for visual design verification. Criteria 1–5 are met
for byte transport only — an agent never knows it cannot see; criteria
6–18 are unbuilt.

The implementation must:

- Render the managed repository's UI through a **declared command**
  supplied by policy, never a bundled headless browser.
- Cover states design actually breaks in, not a single screenshot.
- Judge from rendered pixels plus design intent, and from nothing else.
- Carry the verdict and its captures as durable, ledger-backed evidence.

A deterministic tier exercises the machinery against fixture capture
sets; a judgement tier exercises it against a real renderer and a
real vision model on demand.

## Decision

### D1. Renderer is a bounded declared capture command for web UI only

`ralph.visual.policy_facts` parses a bounded managed-repo `design_capture_command` from
managed-repo policy. `ralph.mcp.tools.workspace._media_capture`
executes the declared argv through `ralph.executor.process` with the
named `target` injected as `{target}` (or as the trailing argv).
Ralph validates the command at the trust boundary, rejects shell
fragments and path traversal in the target name, and never bundles
or invokes a headless browser itself. A no renderer or non-web UI
fails closed with a recorded visual-review blocker rather than a
partial or guessed capture.

### D2. Capture matrix is the unit of evidence

A `design_capture_command` produces a **capture set** — a matrix of
viewport × theme × state per target. The matrix is declared once per
repo from `policy_facts`. A single screenshot is never admissible
visual evidence. A declared cell that fails to render fails the whole
request.

### D3. Server mints evidence, agents do not

Every capture carries a `ralph://media/{artifact_id}` handle on the
criterion 5 wire ledger. Geometry and sha256 are server-computed; the
agent never writes its own captures into evidence. An agent that does
is rejected at the proof-validation boundary.

### D4. Verdict is exactly three inputs

A design verdict accepts ONLY:

1. The retained **before** capture set (run-owned, run/cycle/target/
   matrix-keyed manifest; immutable after the first developer
   subprocess launches).
2. A complete **after** capture set (same matrix, post-change).
3. The **design intent** text from the plan item plus managed-repo
   declarations.

Source, diff, DOM, stylesheets, and any other judgement input are
rejected by typed validation at the artifact boundary. The verified
verdict cites capture IDs and regional pixel rectangles already on
the ledger; an absent handle fails validation.

### D5. Pre-change baseline is run-scoped and immutable

`ralph.visual.capture_lifecycle` owns the comparative baseline. After
the MCP bridge establishes a run ID but before the first developer
subprocess can mutate the workspace, it captures and retains the
complete `before` matrix. Retries and continuations reuse it; the
baseline never overwrites after workspace mutation. A verdict whose
`before` cannot be sourced from a retained manifest fails closed.

### D6. Review lane retains named human verdict authority

Criteria 9 and 10 revisit `testing-policy.md` and
`design-system-policy.md`. A capture-backed criterion 8 verdict is
agent-produced evidence and requires the named human review verdict;
it does not close the review lane autonomously. Unverified evidence
stays in the human queue. Developer prompt guidance only directs this
capture and comparison workflow; no other phase is guided by it.
The developer prompt guidance states the prohibition to the agent, and
`ralph.visual.design_verdict` enforces it at the artifact boundary:
a verdict whose inputs smuggle in source, diff, DOM, or stylesheet
material is rejected by typed validation (D4).

### D7. Two visual smoke tiers

- **Deterministic tier** — offline, stubbed judge, fixture capture
  sets. Lives in `tests/test_visual_smoke.py` (and the focused
  capture/verdict/evidence suites). Carries the `smoke` marker; runs
  under `make verify` via the budget-tracked `make
  test-visual-smoke` target. The 60 s combined budget
  (`_TOTAL_TEST_BUDGET_SECONDS`) is immutable; the tier is
  redesigned when it does not fit.
- **Judgement tier** — real renderer, real vision model, on demand.
  `ralph visual-judgement` makes this lane explicit. Recorded as a dated
  artifact. Never a blocking gate (a nondeterministic judge wired as a
  blocker is the exact failure mode a later run weakens to green).

### D8. Per-call agent attribution

Capability and evidence resolve per media call. The wire-ledger row
carries an authenticated `agent_id` (parent or delegated subagent)
plus the resolved `provider`/`model_id`/`delivery_mode`. A parent
and its subagent remain distinguishable; session-level model
guesses no longer gate image delivery.

### D9. Delegation is declared per transport

A transport that cannot spawn a subagent says so at registration
(`ralph.agents.delegation_capabilities`). A genuinely blind parent
fails criteria 13/15; criterion 3's `UNSUPPORTED` warning keeps the
underlying media call usable but never grades perception.

## Consequences

### Positive

- Visual criteria become evidence, not aspirations.
- The chain is provable offline at the deterministic tier; on-demand
  judgement is a dated artifact, not a flapping gate.
- Policies align: appearance assertions never prove design quality;
  design-system, UX, and accessibility policies all share the same
  rule.

### Negative

- The renderer is the managed repo's responsibility; capture
  quality becomes the managed repo's problem (D1).
- Adding eight vision-capable transports to the smoke suite spends
  against the 60 s budget; the deterministic tier absorbs them
  offline (D7).
- A non-`covered` transport must declare so at registration, or
  registration closes; a future transport cannot quietly inherit
  "unaccounted".

### Neutral

- The `ralph.visual` component is new. It joins `ralph.mcp`,
  `ralph.pipeline`, and `ralph.skills` as the fourth trust-boundary
  carrier.

## Trust Boundaries

1. **Declared command** (`design_capture_command`) — the managed
   repo. Validated at `ralph.visual.policy_facts` and at
   `_media_capture.execute_capture_cell`.
2. **Capture argv execution** — `ralph.executor.process`. Bounded
   timeout, no shell-string interpretation, target injection only.
3. **Server-minted handle** — `ralph.mcp.multimodal.resources`. The
   only path that mints `ralph://media/{artifact_id}` artifacts.
4. **Verdict inputs** — `ralph.visual.design_verdict`. Rejects any
   input beyond the three inputs above by typed validation.
5. **Per-call agent identity** — `ralph.mcp.server._wire_ledger`.
   Every record carries `agent_id` plus resolved identity and
   delivery mode.

## Owners

| Boundary | Owner | Detection seam |
|---|---|---|
| Declared command | managed repo (`design-system-policy.md`) | `ralph.visual.policy_facts` |
| Capture argv execution | `ralph.executor.process` | `_media_capture.py` |
| Server-minted handle | `ralph.mcp.multimodal.resources` | `_wire_ledger.append_wire_record` |
| Verdict inputs | `ralph.visual.design_verdict` | `tests/test_visual_verdict.py` |
| Per-call agent identity | `ralph.mcp.server._wire_ledger` | `_wire_ledger_capture.py` |

## Dependency Direction

```
PROMPT.md / .agent/PRODUCT_CRITERIA.md
        ↓
ralph.visual (policy, capture_request, capture_cell, design_verdict)
        ↓
ralph.mcp.tools.workspace._media_capture
        ↓
ralph.mcp.multimodal + ralph.mcp.server._wire_ledger
        ↓
ralph.executor.process + ralph.workspace
```

The dependency direction is one-way. Visual policy never depends on a
specific renderer; `ralph.mcp` never depends on `ralph.visual`. The
ledger is the only downstream carrier for both.

## Assumptions

- A1. The managed repo supplies a `design_capture_command` for web UI only. If
  it does not, or the UI is non-web, capture paths fail closed with a recorded
  visual-review blocker and visual criteria remain unimplemented for that repo.
- A2. The renderer argv returns PNG. Other formats are rejected at
  the trust boundary.
- A3. The retained `before` manifest is durable for the run's
  lifetime. A workspace wipe is a run boundary; capture resets.
- A4. Stub-driven smoke coverage satisfies criterion 5 for
  perception only when the perception secret is in fixtures, never
  in real production runs. Real-vendor coverage is the judgement
  tier and remains dated on-demand evidence.

## References

- `.agent/PRODUCT_CRITERIA.md` criteria 6–18 (binding spec).
- `docs/architecture/adr-0001-interrupt-architecture.md` for ADR
  format precedent.
- `docs/ralph-workflow-policy/testing-policy.md` and
  `design-system-policy.md` (review-lane, suite-admission, and
  appearance-assertion rules this ADR revisits).
