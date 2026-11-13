# ADR-0002: Visual design verification — renderer, capture, verdict, ledger

- **Scope:** Maintained Python package (`ralph-workflow/ralph/`)

## Architecture

### Renderer is a bounded declared capture command for web UI only

`ralph.visual.policy_facts` parses a bounded managed-repo `design_capture_command` from
managed-repo policy. `ralph.mcp.tools.workspace._media_capture`
executes the declared argv through `ralph.executor.process` with the
named `target` injected as `{target}` (or as the trailing argv).
Ralph validates the command at the trust boundary, rejects shell
fragments and path traversal in the target name, and never bundles
or invokes a headless browser itself. A no renderer or non-web UI
fails closed with a recorded visual-review blocker rather than a
partial or guessed capture.

### Capture matrix

A `design_capture_command` produces a **capture set** — a matrix of
viewport × theme × state per target. The matrix is declared once per
repo from `policy_facts`. A single screenshot is never admissible
visual evidence. A declared cell that fails to render fails the whole
request.

### Server-minted evidence

Every capture carries a `ralph://media/{artifact_id}` handle on the
criterion 5 wire ledger. Geometry and sha256 are server-computed; the
agent never writes its own captures into evidence. An agent that does
is rejected at the proof-validation boundary.

### Verdict inputs

A design verdict accepts ONLY:

1. The retained **before** capture set (run-owned, run/cycle/target/
   matrix-keyed manifest; immutable after the first developer
   subprocess launches).
2. A complete **after** capture set (same matrix, post-change).
3. The **design intent** text from the plan item plus managed-repo
   declarations.

Source, diff, DOM, stylesheets, and any other judgement input have no
place in a verdict. **The automated enforcement of that rule is narrower
than the rule**. The automated check is a case-insensitive,
word-boundary regex over `## Design Intent` in the `design_verdict`
markdown spec (`ralph/mcp/artifacts/markdown/specs/design_verdict.py`,
diagnostic `DV008`). That is a wording heuristic, not a typed
boundary: a paraphrase that names a code artifact with an unlisted
word passes it. The typed three-input contract does exist in code as
`ralph.visual.design_verdict.DesignVerdict`, but **no production path
constructs it** — it is reached only from `tests/test_visual_verdict.py`,
so it constrains nothing an agent actually submits.

What does hold structurally is the evidence chain, not the input
prohibition: the verdict cites capture IDs and regional pixel
rectangles already on the ledger, every `capture_id` cited in a
finding must appear in the artifact's own `cell_ids` (`DV003`), and a
capture handle the active-run wire ledger cannot authenticate fails
validation (`DV013`).

### Run-scoped immutable baseline

`ralph.visual.capture_lifecycle` owns the comparative baseline. After
the MCP bridge establishes a run ID but before the first developer
subprocess can mutate the workspace, it captures and retains the
complete `before` matrix. Retries and continuations reuse it; the
baseline never overwrites after workspace mutation. A verdict whose
`before` cannot be sourced from a retained manifest fails closed.

### Human review authority

Criteria 9 and 10 apply the rules in `testing-policy.md` and
`design-system-policy.md`. A capture-backed criterion 8 verdict is
agent-produced evidence and requires the named human review verdict;
it does not close the review lane autonomously. Unverified evidence
stays in the human queue. Developer prompt guidance only directs this
capture and comparison workflow; no other phase is guided by it.
The developer prompt guidance states the prohibition to the agent
(`ralph/prompts/templates/shared/_developer_iteration_guidance.j2`:
"code, DOM, CSS, and functional appearance assertions are not
visual-verdict evidence"), and the `design_verdict` markdown spec
(`ralph/mcp/artifacts/markdown/specs/design_verdict.py`) enforces it at
the artifact boundary: a `## Design Intent` that pivots the review into
a code-reading task is rejected with diagnostic `DV008` when the
artifact is submitted.

### Visual smoke tiers

- **Deterministic tier** — offline, stubbed judge, fixture capture
  sets. Lives in `tests/test_visual_smoke.py` (and the focused
  capture/verdict/evidence suites). Carries the `smoke` marker; runs
  under `make verify` via the budget-tracked `make
  test-visual-smoke` target. The 60 s combined budget
  (`_TOTAL_TEST_BUDGET_SECONDS`) is immutable; the tier is
  redesigned when it does not fit.
- **Judgement tier** — real renderer, real vision model, on demand.
  `ralph visual-judgement` makes this lane explicit. Recorded as a dated
  artifact. Never a blocking gate.

### Per-call agent attribution

Capability and evidence resolve per media call. The wire-ledger row
carries an authenticated `agent_id` (parent or delegated subagent)
plus the resolved `provider`/`model_id`/`delivery_mode`. A parent
and its subagent remain distinguishable; session-level model
guesses do not gate image delivery.

### Per-transport delegation

A transport that cannot spawn a subagent says so at registration
(`ralph.agents.delegation_capabilities`). A genuinely blind parent
fails criteria 13/15; criterion 3's `UNSUPPORTED` warning keeps the
underlying media call usable but never grades perception.

## Trust Boundaries

1. **Declared command** (`design_capture_command`) — the managed
   repo. Validated at `ralph.visual.policy_facts` and at
   `_media_capture.execute_capture_cell`.
2. **Capture argv execution** — `ralph.executor.process`. Bounded
   timeout, no shell-string interpretation, target injection only.
3. **Server-minted handle** — `ralph.mcp.multimodal.resources`. The
   only path that mints `ralph://media/{artifact_id}` artifacts.
4. **Verdict inputs** — `ralph/mcp/artifacts/markdown/specs/design_verdict.py`.
   A submitted `design_verdict` markdown artifact is the only channel a
   verdict arrives on, so this spec is the boundary in practice: it
   rejects code-reading wording in `## Design Intent` (`DV008`) and
   capture handles the active-run ledger cannot authenticate
   (`DV013`). It is a **partial** boundary — the typed three-input
   contract in `ralph.visual.design_verdict` is not wired into any
   production path, so no code enforces the three-input rule
   itself.
5. **Per-call agent identity** — `ralph/mcp/server/_wire_ledger.py`.
   Every record carries `agent_id` plus resolved identity and
   delivery mode.

## Owners

| Boundary | Owner | Detection seam |
|---|---|---|
| Declared command | managed repo (`design-system-policy.md`) | `ralph.visual.policy_facts` |
| Capture argv execution | `ralph.executor.process` | `_media_capture.py` |
| Server-minted handle | `ralph.mcp.multimodal.resources` | `_wire_ledger.append_wire_record` |
| Verdict inputs | `ralph/mcp/artifacts/markdown/specs/design_verdict.py` | `tests/test_visual_evidence_artifact.py` |
| Per-call agent identity | `ralph/mcp/server/_wire_ledger.py` | `_wire_ledger_capture.py` |

## Dependency Direction

```
PROMPT.md / .agent/PRODUCT_CRITERIA.md
        ↓
ralph.visual (policy, capture_request, capture_cell, design_verdict)
        ↓
ralph.mcp.tools.workspace._media_capture
        ↓
ralph.mcp.multimodal + ralph.mcp.server.wire_ledger
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
- `docs/ralph-workflow-policy/testing-policy.md` and
  `design-system-policy.md` (review-lane, suite-admission, and
  appearance-assertion rules).
