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

The following lines from `ralph-workflow/ralph/project_policy/starters/testing-policy.md` are policy constraints; they cannot be changed by an experimental admission rule:

- **Line 264** (BROWSER-TIME BUDGET definition, verbatim):
  > the default-lane browser time budget is a measured value (declared in this policy's `RALPH-FACT: browser_budget_seconds`).

- **Line 422** (RALPH-FACT key):
  `RALPH-FACT: browser_budget_seconds: PROJECT-FACT-UNRESOLVED`

- **Lines 271–280** (admission-rule prose, verbatim):

  > BELOW the measured budget, a new browser journey is admitted WITHOUT any mandatory time offset elsewhere in the lane; the distinct-browser-only-risk requirement above is the only qualification, and the cost-neutral offset rule (every admitted default-lane addition MUST be budget-neutral through a recorded REMOVE / MERGE / REPLACE) is the only experimental relaxation. AT or OVER the measured budget, admission requires BOTH a time offset elsewhere in the lane AND a distinct-risk justification naming the browser-only risk the retained journey covers that no other check covers. The aggregate ceiling (`default_budget_seconds`) is never raised; the rule only changes how new browser journeys are admitted against the ceiling.

- **policy-portfolio.toml line 2** (aggregate ceiling, verbatim):
  `default_budget_seconds = 60.0`

### Two distinct falsification outcomes

The B5 experiment measures both limits independently.

`browser_budget_seconds` (RALPH-FACT, line 422) is the **default-lane BROWSER time budget**: an aggregate measure of total browser time on the default lane, used as the measured value for the BELOW/AT-or-OVER admission decision. It is NOT itself a second RALPH-FACT limit — it is the per-lane measured benchmark.

`default_budget_seconds` (portfolio.toml line 2, value 60.0) is the **AGGREGATE test-budget ceiling**: the never-raised aggregate cap on total default-lane browser time, carried by the portfolio. A run whose total default-lane browser time exceeds this ceiling falsifies this outcome regardless of which admission rule was applied.

#### 3a. Browser-lane budget falsification

**Falsified if:** a distinct browser-only risk loses coverage — or a retained journey duplicates another check — when the relaxed BELOW-budget admission rule is applied under the experimental arm.

The experiment runs two task arms on the browser-sensitive category:
- **Control arm:** cost-neutral admission (every admitted default-lane addition is budget-neutral via recorded REMOVE/MERGE/REPLACE).
- **Treatment arm:** relaxed BELOW-budget admission (distinct-browser-only-risk justification only, no mandatory time offset).

Falsification: a distinct browser risk loses coverage, or a journey is admitted that another check already covers, under the treatment arm.

#### 3b. Aggregate ceiling falsification

**Falsified if:** total default-lane browser time exceeds `default_budget_seconds` (60.0) under **either** admission rule.

The aggregate ceiling is never raised by any experimental admission rule. Any run whose total default-lane browser time exceeds 60.0 seconds falsifies this outcome regardless of which admission rule was applied.

---

## 4. Held constants

| Constant | Value | Policy / Operational |
|---|---|---|
| Model | Same model, same version, frozen prompt | Policy |
| Harness | Same test harness (Playwright for browser, pytest otherwise) | Policy |
| Tools | Same toolchain versions (python, node, browser) | Policy |
| Time budget | 60-second aggregate ceiling (`default_budget_seconds` = 60.0, portfolio.toml line 2) — never raised by B5 cost-neutral offset | Policy |
| Immutable behavioral checks | Kernel rules: truthful evidence, fail-closed verification, bounded execution, no weakening of checks, explicit unverified-claim reporting | Policy |

**Shared budget reminder:** The 60-second test budget is the AGGREGATE ceiling (portfolio.toml line 2: `default_budget_seconds = 60.0`). No experimental admission rule — including the B5 cost-neutral offset — raises it. Any run whose total default-lane browser time exceeds 60.0 seconds falsifies the aggregate-ceiling outcome regardless of which admission rule was applied.

Operational tunable items (per replay): run count, task selection within categories, reviewer assignment (blinding).

---

## 5. Comparison: v4 vs. v5

| Dimension | v4 (baseline) | v5 (treatment) |
|---|---|---|
| Policy pack | Frozen v4 shipped pack at commit `c67e7536fe8b97831c72d3e5d87644c26b4b2342` | Current v5 pack at commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`: execution-kernel.md kernel + selectively loaded conditional starters |
| Reviewers | Blind to treatment arm | Blind to treatment arm |
| Pack identity | Not disclosed to reviewers until after scoring | Same |

The v4 baseline is pre-change and frozen. The v5 treatment is the current pack with the v5 kernel and selectively loaded controls (not the full shipped set — only those whose triggers fired in the run context).

**v4 SHA:** `c67e7536fe8b97831c72d3e5d87644c26b4b2342` (parent of schema-bump commit `3b88fd1dc`, "feat(policy)!: adopt v5 execution-kernel schema with guard inventories and three-dimension readiness")
**v5 SHA:** `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (HEAD at time of plan write, "docs(policy): anchor v5 starter guards and relocate paired-replay plan")

---

## 6. Run count

- **Minimum:** 3 runs per pack per category × 6 categories × 2 packs = **36 total runs**.
- **Recommended:** 5 runs per pack per category = **60 total runs**.

**Run-count rules:**
- A crashed or timed-out run counts as a **failure on the pack that caused it**. It is not a sample to be replaced.
- Only an **invalid run** (model/harness failure, network partition) is replaced.
- Replacement is **treatment-blind** and documented in the run ledger.
- Replacement does not change the run count denominator for rubric scoring.

### 6.5 Per-run agent time budget

Each individual agent run (one task, one pack) is capped at **30 minutes wall-clock time**.

- **Budget:** 30 minutes per run, exclusive of harness setup and teardown.
- **Kill mechanism:** a watchdog process (per-run subprocess group) terminates the agent subprocess tree on wall-clock expiry. Exit code is captured as `TIMEOUT` and the run is recorded as failed in the run ledger.
- The per-run cap does not accumulate across runs; each run starts fresh.
- The aggregate ceiling (`default_budget_seconds = 60.0`) is a separate, orthogonal budget covering harness/browser time, not agent execution time.

---

## 7. Blinded-review input

The reviewer panel receives a **redacted input package** that does not disclose which response is v4 or v5:

**Package contents:**
1. **read.log** — the per-run policy-file-read ledger (`tmp/policy-v5-replay-runs/<run_id>/read.log`). Both v4 and v5 runs are included; filenames use treatment codes only (see item 3).
2. **Scored rubric** — the nine-metric rubric (§7 table) with scores for each run, labeled by treatment code.
3. **Treatment-code assignment** — an unblinded coordinator (not on the review panel) holds the mapping:

   | Code | Actual pack |
   |------|-------------|
   | R1 | v4 (baseline) or v5 (treatment), per paired run |
   | R2 | the other pack in the pair |

   Reviewers see only R1/R2 labels; they do not know which is v4 or v5 until after scoring is complete and the blind is broken by the coordinator.

**What reviewers assess:** functional output correctness, protected outcome preservation, verification truthfulness, rework burden, and elapsed time — against the rubric scores, not against pack identity.

---

## 8. Per-run policy-file-read capture

### Capture mechanism

Every run records which starter files the agent actually loaded. The canonical seam is `starters.read_starter(name)` — the Ralph workflow call that materializes a starter's content into the agent context.

Each call to `starters.read_starter(name)` is logged **immediately** (not batched) to a per-run ledger file:

```
tmp/policy-v5-replay-runs/<run_id>/read.log
```

**Format (one line per load):**
```
<timestamp> <starter_name>
```

Where:
- `<timestamp>` is ISO-8601 UTC to the millisecond (e.g., `2025-06-20T14:32:01.003Z`)
- `<starter_name>` is the exact starter name passed to `read_starter` (e.g., `testing-policy`, `security-policy`, `execution-kernel`)

**Example ledger entry:**
```
2025-06-20T14:32:01.003Z testing-policy
2025-06-20T14:32:01.045Z execution-kernel
```

**Summing:** At run completion, the ledger is sorted by timestamp and de-duplicated (same starter loaded multiple times is logged each time but counted once for applicability judgment). The coordinator's summary script outputs:
- Total unique starters loaded
- Count of loads per starter
- Sequence of first loads (for selective-applicability audit)

**Selective-applicability judgment (B2):** A conditional starter whose trigger did not fire during the run — yet whose name appears in `read.log` — is a B2 violation: the starter was loaded despite not being applicable.

**Directory size is not a proxy.** Only the `read.log` establishes which files were active.

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

---

## D. Task snapshots and immutable behavioral checks

Each category uses task snapshots seeded under `tmp/policy-v5-replay-snapshots/<category>/`.
Snapshots are **immutable after seeding** — a snapshot's task description, fixture path, and behavioral checks are locked to a commit hash and never modified in-place. New versions are placed in a new subdirectory with a new commit reference.

### D.1 Feature

**Snapshot 1 — `add-feature-flag-journey`**
- Path: `tmp/policy-v5-replay-snapshots/feature/add-feature-flag-journey/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (ralph-workflow fixture branch `locked/feature-add-feature-flag`)
- Description: Add a new end-to-end journey under a new feature flag, with a distinct risk uncovered by existing unit or integration tests.

**Snapshot 2 — `new-auth-provider-journey`**
- Path: `tmp/policy-v5-replay-snapshots/feature/new-auth-provider-journey/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/feature-new-auth-provider`)
- Description: Add a browser journey exercising a new OAuth2 provider integration.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/e2e/ --tb=short -q                # must exit 0
ralph --diagnose                            # must exit 0
git diff --exit-code                        # no uncommitted policy changes
git log --oneline HEAD~1..HEAD | grep -qE 'feat|fix'  # at least one commit
```

### D.2 Regression

**Snapshot 1 — `smoke-test-regression`**
- Path: `tmp/policy-v5-replay-snapshots/regression/smoke-test-regression/`
- Seeded from: commit `c67e7536fe8b97831c72d3e5d87644c26b4b2342` (branch `locked/regression-smoke`)
- Description: Re-run a previously failing smoke-test fixture to confirm protected outcomes are still enforced.

**Snapshot 2 — `auth-token-regression`**
- Path: `tmp/policy-v5-replay-snapshots/regression/auth-token-regression/`
- Seeded from: commit `c67e7536fe8b97831c72d3e5d87644c26b4b2342` (branch `locked/regression-auth-token`)
- Description: Re-run a session that previously failed due to token-expiry edge case.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/smoke/ -q                              # must exit 0 (no new failures)
ralph --diagnose                                    # must exit 0
git diff --exit-code                                # no uncommitted policy changes
python -c "import json; assert json.load(open('evidence/report.json'))['passed']"  # evidence complete
```

### D.3 Refactor

**Snapshot 1 — `rename-service-boundary`**
- Path: `tmp/policy-v5-replay-snapshots/refactor/rename-service-boundary/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/refactor-rename-boundary`)
- Description: Rename a service boundary and update all affected stubs; verify guard-inventory accuracy.

**Snapshot 2 — `extract-helper-module`**
- Path: `tmp/policy-v5-replay-snapshots/refactor/extract-helper-module/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/refactor-extract-helper`)
- Description: Extract a shared helper from two modules; update all call sites.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/ -q                                           # must exit 0 (no regressions)
git diff --name-only | grep -qE '\.(py|md|toml)$'          # policy files touched
ralph --diagnose                                            # must exit 0
python -c "import ast; ast.parse(open('src/modified.py').read())"  # valid syntax
```

### D.4 Integration-heavy

**Snapshot 1 — `multi-step-api-chain`**
- Path: `tmp/policy-v5-replay-snapshots/integration-heavy/multi-step-api-chain/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/integration-api-chain`)
- Description: Multi-step API call chain with auth headers, token refresh, and retry logic.

**Snapshot 2 — `db-migration-plus-query`**
- Path: `tmp/policy-v5-replay-snapshots/integration-heavy/db-migration-plus-query/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/integration-db-migration`)
- Description: Schema migration followed by a query that depends on the new columns.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/integration/ -q                         # must exit 0
ralph --diagnose                                    # must exit 0
git diff --exit-code                                # no uncommitted policy changes
curl -s http://localhost:$(cat port)/health | grep -q '"status":"ok"'  # service healthy
```

### D.5 Security-sensitive

**Snapshot 1 — `token-handling-path`**
- Path: `tmp/policy-v5-replay-snapshots/security-sensitive/token-handling-path/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/security-token-handling`)
- Description: Exercise token generation, storage, and refresh paths; verify no secrets in logs.

**Snapshot 2 — `input-sanitization-path`**
- Path: `tmp/policy-v5-replay-snapshots/security-sensitive/input-sanitization-path/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/security-input-sanitization`)
- Description: Submit payloads with injection patterns; verify sanitization is applied end-to-end.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/security/ -q                           # must exit 0
ralph --diagnose                                    # must exit 0
git diff --exit-code                                # no uncommitted policy changes
python -c "import subprocess; r=subprocess.run(['git','log','--oneline','-1'],capture_output=True); assert r.returncode==0"  # clean git state
grep -rE 'password|secret|token' tmp/ 2>/dev/null | grep -qv '.gitignore' && exit 1 || exit 0  # no secrets in tmp
```

### D.6 Browser-sensitive

**Snapshot 1 — `dom-interaction-journey`**
- Path: `tmp/policy-v5-replay-snapshots/browser-sensitive/dom-interaction-journey/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/browser-dom-interaction`)
- Description: E2E journey exercising click, hover, and DOM-state assertions via Playwright.

**Snapshot 2 — `network-timing-journey`**
- Path: `tmp/policy-v5-replay-snapshots/browser-sensitive/network-timing-journey/`
- Seeded from: commit `f760933b2fae095ecdb8bf9f06ae8125a5dd9e90` (branch `locked/browser-network-timing`)
- Description: E2E journey asserting on network request timing and resource load ordering.

**Immutable behavioral checks (both snapshots):**
```
pytest tests/e2e/ -q                                # must exit 0
ralph --diagnose                                    # must exit 0
git diff --exit-code                                # no uncommitted policy changes
playwright test --browser=chromium -q               # browser tests pass
python -c "import json; data=json.load(open('evidence/browser-timing.json')); assert data['total_time'] < 60"  # under aggregate ceiling
```
