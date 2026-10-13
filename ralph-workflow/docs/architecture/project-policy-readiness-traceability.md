# Project-policy readiness traceability (v5)

## What the v5 readiness gate reports

Readiness is now reported across **three dimensions**, not two:

| Dimension | What it proves | When it flips True |
|---|---|---|
| `structure_complete` | The deterministic validator passes — every required core file exists with the right schema marker, headings, facts, commands, citations, and guard inventory. | When `validators.validate_readiness` returns an empty findings list. |
| `implementation_verified` | Every declared `RALPH-COMMAND` gate ran to exit 0 AND the recorded `policy_content_hash` matches the current readiness-evidence signature. A ledger recorded against a pre-edit policy tree cannot certify the present policy. | When every recorded outcome has `exit_code == 0` and the policy-content hash matches. |
| `outcome_evidence_reviewed` | The recorded outcomes cover every declared gate (command-completeness) AND the analysis agent returned `completed`. A declared gate that was never probed is an unreviewed gate. | When every declared `RALPH-COMMAND` is in the ledger AND analysis blessed the policy. |

`structure_complete` is the **caching gate**: the project enters READY only when
this dimension is True. `implementation_verified` and
`outcome_evidence_reviewed` are **advisory dimensions**: the project
remains structurally READY without them, but agents and humans see
that semantic correctness is *separate* from structural completeness.

## Why three states, not two

The previous behavior reported only `READY` / `NOT READY`. That
projection collapsed two different facts into one label:

* "the validator is clean" (a structural property of the policy pack)
* "the declared gates actually prove the project's claims" (a
  semantic property of the run that produced the evidence)

A green gate that never ran is a worse signal than a red gate the
operator can see. Three states separate these facts so a structural
READY is not presented as semantic correctness.

## Surfaces that report the three dimensions

| Surface | File | What the user sees |
|---|---|---|
| CLI emit | `ralph/project_policy/cli_integration.py` | `readiness-assessment: structure_complete=..., implementation_verified=..., outcome_evidence_reviewed=...`, plus a note naming the specific false dimensions when `structure_complete` is true but semantic verification is incomplete. |
| Status bar | `ralph/project_policy/status_bar.py` | The footer carries the per-dimension state during the run (`Policy: S*/I*/O*` or dash variants), not a single READY/NOT-READY label. |
| AGENTS.md managed block | `ralph/project_policy/agents_md.py` | The condensed READY block reports all three dimensions (`structure complete=..., implementation verified=..., outcome evidence reviewed=...` or `unknown`), distinguishing structural READY from semantic correctness. |
| Remediation prompt | `ralph/project_policy/remediation.py` + `analysis.py` | The materialized prompts report the current values (`Current readiness: structure_complete=..., implementation_verified=..., outcome_evidence_reviewed=...` or `unknown`), keeping agents informed of what has been verified. |

`structural READY remains the cache gate` — a project is not READY if
`structure_complete` is False. A project is `READY` if all three are
True. A project is `READY (advisory)` if only `structure_complete` is
True; agents see the gap in the result body.

## Selective applicability (B2)

The kernel is loaded on every change. Domain policies are loaded
selectively: the agent reads the per-starter "Applicability"
section and the AGENTS.md managed block to decide which conditional
policy to apply. The default for a conditional starter is "do not
load it" — the starter's own trigger must fire.

The `execution-kernel.md` starter is the v5 generic layer; every
other starter shrinks its `## AI execution instructions` to
domain-specific steps plus a kernel pointer. The kernel rules are
not rephrased in domain starter body prose.

## Guard inventory (B9)

Every applicable starter (core and conditional) carries a
`## Guard inventory` section. The table is the only way a domain
rule survives a kernel bump: a rule in body prose without an
inventory row is not enforceable. The grammar is pinned:

| Guard | Owner | Protected outcome | Detected condition | Retirement trigger |
|---|---|---|---|---|

Five cells per row; pipes balanced; the `Detected condition` cell
must mirror any broad-guarantee phrase in `Protected outcome` (the
deterministic claim-scope rule, B7). The validator emits a stable
`RWP-INVENTORY:*` finding on a missing or malformed table.

## Portfolio schema v2

The shipped `policy-portfolio.toml` carries schema version `v2`.
v2 adds two required control fields:

* `detects` — the observable condition the detector actually reports.
* `retirement_trigger` — when to retire the control.

A control whose `protected_outcome` carries a broad-guarantee phrase
that does NOT also appear in `detects` is rejected on parse. Frozen
v1 packs keep v1 validation (the `core_files_for_version` helper
selects the manifest a frozen pack is validated against, so a
frozen v1 portfolio and a v4-frozen policy pack are both
acceptable).

## Frozen-pack manifest (B2)

A frozen policy pack is validated against the manifest for the
schema version in its freeze marker, NOT the current manifest:

* `freeze v4` → the v4 manifest (the original eleven core files).
  `security-policy.md` is still required; `execution-kernel.md` is
  exempt (introduced in v5).
* `freeze v5` → the v5 manifest (twelve core files including
  `execution-kernel.md`).
* Unfrozen → the current `CORE_POLICY_FILES`.

A frozen v4 pack missing `security-policy.md` stays NOT READY
because the v4 manifest still requires it. Only files introduced
AFTER a frozen version are exempt.

## Verification commands

Run the focused project-policy tests:

```bash
cd ralph-workflow
python -m pytest tests/project_policy/ -q
```

Run the full gate:

```bash
cd ralph-workflow
make verify
```

Run the v5-specific tests:

```bash
cd ralph-workflow
python -m pytest tests/project_policy/test_v5_kernel.py -q
```

## What ships in the v5 contract

* 12 required core files (was 11): add `execution-kernel.md`.
* 10 conditional files (unchanged).
* Portfolio schema v2: every control needs `detects` and
  `retirement_trigger`.
* `## Guard inventory` table on every applicable starter.
* Three readiness dimensions on every surface.

## What does NOT ship in v5

* No hand edits under `docs/ralph-workflow-policy/`. The consumer
  docs are auto-regenerated by the readiness preflight; a hand edit
  gets overwritten on the next run.
* No bypass for the kernel. The five rules are absolute.
* No "READY" claim without `structure_complete` True.
