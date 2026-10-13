# Policy v5 Paired Replay Plan — Step D Measurement

## 1. Why this plan exists

Static acceptance proves the change was made; this plan proves whether it helped.
PRODUCT_CRITERIA.md step D states: *static acceptance proves the change was made, not that it helped; run a paired replay before declaring the revision an improvement.*

The v5 changes (markers.py lines 65–99: execution-kernel.md mandatory, guard-inventory tables, portfolio schema v2, three-dimensional readiness reporting, meaning-freeze for gate scripts) alter the policy contract. Whether those alterations improve project outcomes — or introduce regressions — must be measured, not assumed.

---

## 2. Task set (balanced)

The plan covers six categories. Each category requires runs against both v4 and v5 packs.

| Category | Example | What it stresses |
|---|---|---|
| Feature | Add a new end-to-end journey under a new feature flag | New control admission, selective-applicability (B2) |
| Regression | Re-run a failing smoke-test fixture from a prior session | Protected outcomes still enforced, no false-pass |
| Refactor | Rename a service boundary and update affected stubs | Control retirement, guard-inventory accuracy |
| Integration-heavy | Multi-step API call chain with auth and retry | Coverage breadth, no gaps from kernel vs. domain overlap |
| Security-sensitive | Token-handling or input-sanitization path | Security-boundary preservation; no weakening of checks |
| Browser-sensitive | E2E journey exercising DOM interaction or network timing | Browser-budget admission rules; distinct-risk inventory |

The browser-sensitive category is the primary tie-in for the B5 experiment (§3).

---

## 3. B5 browser-budget experiment (MANDATORY — two-limit structure)

### Policy constraints (verbatim citations)

The following two lines from `ralph-workflow/ralph/project_policy/starters/testing-policy.md` are policy constraints; they cannot be changed by an experimental admission rule:

- **Line 422** (RALPH-FACT key): `RALPH-FACT: browser_budget_seconds: PROJECT-FACT-UNRESOLVED`
- **Lines 271–278** (admission-rule prose, verbatim):

  > BELOW the measured budget, a new browser journey is admitted WITHOUT any mandatory time offset elsewhere in the lane; the distinct-browser-only-risk requirement above is the only qualification, and the cost-neutral offset rule (every admitted default-lane addition MUST be budget-neutral through a recorded REMOVE / MERGE / REPLACE) is the only experimental relaxation. AT or OVER the measured budget, admission requires BOTH a time offset elsewhere in the lane AND a distinct-risk justification naming the browser-only risk the retained journey covers that no other check covers. The aggregate ceiling (`default_budget_seconds`) is never raised; the rule only changes how new browser journeys are admitted against the ceiling.

### Two distinct falsification outcomes

The B5 experiment measures both limits independently. `default_budget_seconds` is the **AGGREGATE ceiling** (PRODUCT_CRITERIA.md step D; the testing starter's `browser_budget_seconds` fact is the per-journey measured value).

#### 3a. Browser-lane budget falsification

**Falsified if:** a distinct browser-only risk loses coverage — or a retained journey duplicates another check — when the relaxed BELOW-budget admission rule is applied under the experimental arm.

The experiment runs two task arms on the browser-sensitive category:
- **Control arm:** cost-neutral admission (every admitted default-lane addition is budget-neutral via recorded REMOVE/MERGE/REPLACE).
- **Treatment arm:** relaxed BELOW-budget admission (distinct-browser-only-risk justification only, no mandatory time offset).

Falsification: a distinct browser risk loses coverage, or a journey is admitted that another check already covers, under the treatment arm.

#### 3b. Aggregate ceiling falsification

**Falsified if:** total default-lane browser time exceeds `default_budget_seconds` under **either** admission rule.

The aggregate ceiling is never raised by any experimental admission rule. This is the retained policy constraint. `default_budget_seconds` is the 60-second AGGREGATE test budget ceiling.

**Result:** `default_budget_seconds` is the AGGREGATE ceiling; `browser_budget_seconds` (the measured per-journey value, declared as `RALPH-FACT: browser_budget_seconds: PROJECT-FACT-UNRESOLVED`) is the per-journey benchmark. The B5 experiment measures BOTH limits with distinct outcomes.

---

## 4. Held constants

| Constant | Value | Policy / Operational |
|---|---|---|
| Model | Same model, same version, frozen prompt | Policy |
| Harness | Same test harness (Playwright for browser, pytest otherwise) | Policy |
| Tools | Same toolchain versions (python, node, browser) | Policy |
| Time budget | 60-second aggregate ceiling (`default_budget_seconds`) — never raised by B5 cost-neutral offset | Policy |
| Immutable behavioral checks | Kernel rules: truthful evidence, fail-closed verification, bounded execution, no weakening of checks, explicit unverified-claim reporting | Policy |

**Shared budget reminder:** The 60-second test budget is the AGGREGATE ceiling. No experimental admission rule — including the B5 cost-neutral offset — raises it. Any run whose total default-lane browser time exceeds 60 seconds falsifies the aggregate-ceiling outcome regardless of which admission rule was applied.

Operational tunable items (per replay): run count, task selection within categories, reviewer assignment (blinding).

---

## 5. Comparison: v4 vs. v5

| Dimension | v4 (baseline) | v5 (treatment) |
|---|---|---|
| Policy pack | Frozen v4 shipped pack, restored from tagged commit or snapshot fixture | Current v5 pack: execution-kernel.md kernel + selectively loaded conditional starters |
| Reviewers | Blind to treatment arm | Blind to treatment arm |
| Pack identity | Not disclosed to reviewers until after scoring | Same |

The v4 baseline is pre-change and frozen. The v5 treatment is the current pack with the v5 kernel and selectively loaded controls (not the full shipped set — only those whose triggers fired in the run context).

---

## 6. Run count

- **Minimum:** 3 runs per pack per category × 6 categories × 2 packs = **36 total runs**.
- **Recommended:** 5 runs per pack per category = **60 total runs**.

**Run-count rules:**
- A crashed or timed-out run counts as a **failure on the pack that caused it**. It is not a sample to be replaced.
- Only an **invalid run** (model/harness failure, network partition) is replaced.
- Replacement is **treatment-blind** and documented in the run ledger.
- Replacement does not change the run count denominator for rubric scoring.

---

## 7. Blind scoring rubric

Nine named metrics from PRODUCT_CRITERIA.md step D.4. Each rated 0/1/2.

| Metric | 0 (blocker) | 1 (major) | 2 (minor) |
|---|---|---|---|
| User-visible correctness | Functional output wrong | Minor output gap | Output correct, poor presentation |
| Regressions | New failures introduced | Prior passing behavior degraded | No change |
| Security-boundary preservation | Security check bypassed | Security check weakened | Security check intact |
| Unnecessary abstractions | Extraneous layers added | Some noise, not harmful | No unnecessary abstraction |
| Duplicate tests | Exact duplicate present | Overlapping coverage | Unique coverage |
| Verification truthfulness | False pass recorded | Partial evidence only | Full truthful evidence |
| Human rework | Reviewer must redo significant work | Moderate rework needed | Minimal rework |
| Elapsed time | Slower than baseline | Marginally slower | Same or faster |
| Inference cost | Higher token usage | Slightly higher | Same or lower |

**Sum:** Higher total is better (max 18). Equal or higher v5 mean with no regression is the keep threshold.

**Human-review item (claim-scope):** The substring predicate catches literal broad-guarantee claims. A separate human-review item flags implied broad claims not captured by the automated check. This item is scored separately (pass/fail) and does not enter the numeric sum.

---

## 8. Per-run file-read logging

Every run records which policy files it actually read: the execution-kernel.md plus any conditional starters whose trigger fired in that run context. This log is **appended to the run ledger**, not committed to the repository.

Selective-applicability (B2) is judged against this log: a run that loaded a conditional starter whose trigger did not fire during that run is a **B2 violation** — the starter was not applicable, yet it was loaded.

Directory size is not a proxy for exposure; only the per-run file-read log establishes which files were active.

---

## 9. Keep-or-restore rule

v5 is **kept** only if:
1. v5 mean rubric score ≥ v4 mean (no regression), **AND**
2. At least one dimension improves without worsening any other.

**Restore signal (lower v5 mean):** Restore v4, redesign the change, re-run. "Keep most of it" is not a valid outcome.

**Finer redesign signal (equal mean with one improved + one worsened):** Restore v4, redesign, re-run.

**Retired controls:** Any v5 control that was retired and whose protected failure reappears in the v5 run is added back before v5 ships.

---

## 10. Timebox

Replay is a **separate phase** with its own timebox. It is not part of the v5 implementation timebox. The implementation timebox covers steps A–C; the replay timebox covers step D and must conclude before any v5 ship decision.

---

## 11. References

- Gloaguen et al., *Evaluating AGENTS.md*, v3. Available: <https://arxiv.org/abs/2602.11988v3>
- markers.py v5 history entry, lines 65–99: the five changes and the v4→v5 consequence for existing projects.
