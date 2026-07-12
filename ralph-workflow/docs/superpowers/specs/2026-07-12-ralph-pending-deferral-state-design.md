# RALPH-PENDING — a first-class deferral state — design

Date: 2026-07-12
Status: proposed (in-session)

## Problem

The policy-readiness contract has two terminal states for any gate or fact:
resolved (a real `RALPH-COMMAND` / `RALPH-FACT` value) or inapplicable
(`RALPH-INAPPLICABLE`, meaning *never applies to this project*). It has no
state for **"this is the right gate/fact, it applies, but it cannot be
resolved yet"** — most importantly *the tool is not installed yet*.

This bites hardest on **new projects**, which are the primary case: a fresh
repo legitimately has no test runner installed, no scanner chosen, no secret
manager wired. Today an agent faced with such an item has only bad options:

* Leave the starter placeholder (`PROJECT-FACT-UNRESOLVED`, `TODO`) — which
  **blocks readiness** and, on the next preflight, **re-triggers remediation**.
* Declare a `RALPH-COMMAND` for a tool that is not installed — a **hollow
  gate** that lies about verification.
* Misuse `RALPH-INAPPLICABLE` — which asserts the gate *never* applies, is
  **rejected outright** for `testing-policy.md`, and must match fixed
  "no suitable maintained tool exists" wording for typecheck/lint that
  "not installed yet" does not satisfy.

The intent: a legitimately-deferred item is **just pending**. It must reach
READY (so it does not re-enter remediation), and it is resolved by ordinary
**dev-cycle agents** — which discover the policies through AGENTS.md — when
its trigger is met, *not* by re-running policy remediation.

A secondary, related gap: after READY the dev-facing AGENTS.md block frames
policy upkeep as change-driven ("keep facts current as the project evolves")
and never ties the `review trigger:` convention to a per-cycle obligation to
resolve fired triggers or record newly-discovered facts. Deferred items would
be recorded but never deterministically resurfaced.

## Decision

Add one new marker, **`RALPH-PENDING`**, as the single deterministic deferral
state for **both gates and facts**. It:

* passes the deterministic validator (so a fully-pending new project reaches
  READY and does **not** re-enter remediation);
* carries a machine-checkable structure (real intended tool, an assumed date,
  and a review trigger) so it can never silently become a permanent hollow
  gate;
* is owned by dev-cycle agents, wired into the AGENTS.md ready block and the
  per-file marker documentation so it is resolved during normal development.

Schema stays **v2**: this is an additive loosening. Every existing READY
project stays READY (none contain `RALPH-PENDING`); no migration is required.

## The deferral state — structure (normative)

`RALPH-PENDING` has two surface forms sharing one shape: **intended target +
`(assumed <ISO-date>)` + `review trigger: <condition>`**.

### Gate form

Stands in for `RALPH-COMMAND` / `RALPH-INAPPLICABLE`, including inside a
`RALPH-LANG:` block:

```
RALPH-PENDING: <approved-tool …> (assumed <YYYY-MM-DD>); review trigger: <condition>
```

Example (new project, test runner not installed):

```
RALPH-PENDING: pytest (assumed 2026-07-12); review trigger: once test deps are installed in CI
```

### Fact form

The `RALPH-PENDING` token is the value sentinel of a `RALPH-FACT` line:

```
RALPH-FACT: <key>: RALPH-PENDING (assumed <YYYY-MM-DD>); review trigger: <condition>
```

Example (scanner not yet chosen):

```
RALPH-FACT: secret_scan_command: RALPH-PENDING (assumed 2026-07-12); review trigger: once a secret scanner is selected
```

### Three deterministic checks (reusing existing machinery)

1. **Real intended tool** — *gate form only*. The value's first non-`ENV=`
   token must be on `APPROVED_GATE_TOOLS` (or start with `./` / `bin/`),
   reusing `_command_first_token` / `_command_is_approved`. This guarantees the
   eventual gate is a real tool, not vaporware. (The fact form has no tool
   token and skips this check.)
2. **Dated** — the value must contain `(assumed <ISO-date>)` where the date
   matches `\d{4}-\d{2}-\d{2}` (the same regex the citation review-date check
   uses). The literal `<date>` remains a `PLACEHOLDER_TOKENS` entry, so an
   example copied verbatim still blocks until a real date is substituted.
3. **Triggered** — the value must contain a non-empty `review trigger: …`
   clause naming the condition that resolves the deferral.

A `RALPH-PENDING` line that fails any check emits a stable
`RWP-PENDING:<filename>:<kind>-<n>` finding (kinds: `unapproved`, `undated`,
`no-trigger`, `placeholder`).

`RALPH-PENDING` is deliberately **not** added to `PLACEHOLDER_TOKENS`: it is a
resolved deferral, not an unfilled placeholder. Its own dedicated checks own
its validation.

## Guardrails (why it cannot become a permanent hollow gate)

`RALPH-PENDING` is accepted on **every** policy — including the testing and
verification gates. The project owner's decision: trust dev-cycle agents to
resolve deferrals later rather than block a new project. The looser validator
acceptance is balanced by a *harder* dev-cycle obligation (below), not by a
per-policy prohibition. What keeps a deferral honest:

* **Named real tool** — the gate form's first token is allowlist-checked
  (check 1), so the eventual gate is a real tool.
* **Dated + triggered** — every pending item is visibly provisional and
  carries the condition that resurfaces it (checks 2–3).
* **Hard dev-cycle ownership** — the AGENTS.md ready block makes scanning for
  EVERY `RALPH-PENDING` line and resolving every fired trigger a
  normal-development obligation, and declares leaving a resolvable one a
  policy violation (below).

A well-formed testing deferral is therefore
`RALPH-PENDING: pytest (assumed 2026-07-12); review trigger: once test deps are
installed`. A project may alternatively declare the intended `RALPH-COMMAND`
directly (the validator never executes it, so a named-but-uninstalled tool is
already fine) — deferral is the choice when the tool is not even chosen yet.
`RALPH-INAPPLICABLE` remains reserved for a gate that NEVER applies, and testing
still cannot be marked inapplicable.

## Fully-pending is allowed

With unification, a young policy may have *every* fact and every
non-mandatory gate as `RALPH-PENDING` and still reach READY. This is
intentional and central to the new-project case: there is **no** "at least
one genuinely-resolved fact/gate" rule, which would defeat the exact scenario
this feature exists for. The dated + triggered structure plus dev-cycle
resolution — not a resolved-count floor — is what keeps a pending policy
honest.

## Validator integration

### `markers.py`

* Add `PENDING_MARKER: Final[str] = "RALPH-PENDING:"` and
  `PENDING_SENTINEL: Final[str] = "RALPH-PENDING"` (the fact-value sentinel).
* Add `ID_PENDING: Final[str] = "RWP-PENDING"`.
* Bump `POLICY_CONTRACT_VERSION` so the readiness cache re-validates under the
  new validator (old caches were computed without PENDING awareness).
* `SCHEMA_VERSION` / `POLICY_SCHEMA_MARKER` unchanged (additive).
* `RALPH-PENDING` is NOT added to `PLACEHOLDER_TOKENS`.

### `validators.py`

* New helpers: `_pending_lines()` (parse gate-form values),
  `_pending_fact_values()` (fact-form values), and a shared
  `_check_pending_shape(value, *, require_tool)` returning findings for the
  three checks.
* `_check_commands`: treat a valid gate-form `RALPH-PENDING` as satisfying the
  "at least one gate" requirement on every policy — including the mandatory set
  (`testing-policy.md`, `verification-policy.md`), where presence is satisfied
  by a `RALPH-COMMAND` **or** a `RALPH-PENDING` (never by `RALPH-INAPPLICABLE` /
  `RALPH-REVIEW`, since those gates always apply). `_check_individual_pendings`
  validates shape only; there is no forbidden-on-mandatory rejection.
* `_check_per_language_coverage` / `_lang_blocks`: accept a valid gate-form
  `RALPH-PENDING` inside a `RALPH-LANG:` block as satisfying coverage (like
  `RALPH-INAPPLICABLE`), with the same shape enforcement; an empty/malformed
  pending line in a block emits a coverage finding.
* `_check_placeholders`: recognize a fact-form `RALPH-PENDING` value as a
  resolved-deferred fact (counts toward "the key is present with a value" and
  is exempt from the raw-placeholder rejection) provided it passes
  `_check_pending_shape(require_tool=False)`; a malformed pending fact emits a
  `RWP-PENDING:<file>:fact-<n>` finding.
* `_check_required_fact_keys` is unaffected: a pending fact value is non-empty,
  so each required key still appears exactly once.

## Dev-cycle resolution (closes the discovery gap, balances the looser gate)

Because `RALPH-PENDING` is now accepted everywhere, the dev-cycle obligation is
made deliberately HARD — it is the compensating control. `agents_md.py`
`_AGENTS_READY_TEMPLATE` (read every dev cycle, kept within its ≤10-line
budget) directs agents to, on every change, **scan `docs/ralph-workflow-policy/`
for EVERY `RALPH-PENDING` line and resolve each whose `review trigger:` is now
met** — wiring the real gate or recording the real fact — and declares that
**leaving a resolvable `RALPH-PENDING` in place is a policy violation**, fixed
in normal development and never by re-running remediation. It also directs
recording newly-discovered verified facts even absent a listed trigger.

The 21 per-policy "Maintenance triggers" sections are **not** individually
edited (bounded scope); the ready block plus the remediation-prompt and starter
documentation carry the obligation.

## Documentation (first-class deliverable)

The deferral state is documented wherever an agent or human encounters it, in
one consistent structure, phrased so an AI agent understands *which* state to
pick:

* **Remediation prompt** (`policy_remediation.jinja`) — the authoritative home.
  A new **"Marker states — the vocabulary you author with"** block defines
  every `RALPH-*` state (resolved fact, pending fact, command, inapplicable,
  pending gate, review) and the deferral-vs-never-applies distinction agents
  most often get wrong. Step 4 and the gate/lang bullets teach the
  `RALPH-PENDING` gate and fact forms.
* **Starters** — the fact REPLACE-ME guidance (13 files) and the shared gate
  REPLACE-ME clause (8 files) now name the `RALPH-PENDING` deferral form; the
  security starter's secret-scan and per-language scanner comments call it out
  explicitly. The ad-hoc `none yet (assumed <date>; revisit when <trigger>)`
  wording is replaced by the canonical `RALPH-PENDING` form. (Guidance lives in
  REPLACE-ME comments, which are deleted before READY, so permanent policy is
  not bloated and the starter-enforcement-prose guard stays green.)
* **AGENTS.md ready block** — the hard dev-cycle obligation above.
* **This spec** — the normative structure section other surfaces point back to.

## Tests

* Gate-form `RALPH-PENDING` (approved tool + assumed date + review trigger)
  validates clean on a non-mandatory policy.
* Each malformed variant emits its stable finding: `unapproved` (bad first
  token), `undated` (missing/invalid `(assumed …)`), `no-trigger` (missing
  `review trigger:`), `placeholder` (contains a `PLACEHOLDER_TOKENS` token,
  including literal `<date>`). The `undated` case uses a clean single-defect
  isolate so it asserts ONLY `undated` fires.
* A malformed gate-form AND a malformed fact-form pending each block through
  the FULL per-file validator (`_check_policy_file`), not only the helpers —
  guarding the `_check_commands` / `_validate_existing_policy_file` wiring so a
  deleted call cannot let a malformed deferral reach READY.
* The dev-cycle obligation is guarded: the condensed AGENTS.md ready block must
  contain the `RALPH-PENDING` scan/resolve mandate (`RALPH-PENDING`,
  `review trigger`, `violation`), so the compensating control cannot be
  silently dropped.
* `RALPH-PENDING` gate ACCEPTED on `testing-policy.md` (no forbidden finding),
  proving deferral is allowed on the mandatory gates.
* Fact-form `RALPH-PENDING` validates clean, counts as the required key, and
  is exempt from raw-placeholder rejection; a malformed pending fact emits
  `fact-<n>`.
* Per-language `RALPH-PENDING` inside a `RALPH-LANG:` block satisfies coverage.
* **Integration proof for the core intent:** a project whose facts and
  non-mandatory gates are ALL valid `RALPH-PENDING` reaches READY
  (`validate_readiness` returns `[]`) — proving pending does not block and
  does not re-enter remediation.
* Reconcile `tests/project_policy/test_starter_enforcement_prose.py` if it
  asserts the old provisional phrasing.

## Out of scope

* Staleness enforcement (rejecting an "old" assumed date): the validator
  cannot meaningfully judge "too old"; the review trigger plus dev-cycle
  ownership handle it.
* Editing each per-policy "Maintenance triggers" section individually.
* Any schema-version bump or migration (the change is additive).
