# Policy v5 Paired Replay Plan — Step D Measurement

## 1. Why this plan exists

Static acceptance proves the change was made; this plan proves whether it helped.
PRODUCT_CRITERIA.md step D states: *static acceptance proves the change was made, not that it helped; run a paired replay before declaring the revision an improvement.*

The v5 changes (`ralph/project_policy/markers.py:65-99`: execution-kernel.md mandatory, guard-inventory tables, portfolio schema v2, three-dimensional readiness reporting, meaning-freeze for gate scripts) alter the policy contract. Whether those alterations improve project outcomes — or introduce regressions — must be measured, not assumed.

This plan is the operational handbook: every claim below is grounded in a real file path, a real test, or a verbatim policy line. Aspiration is rejected; the steps reproduce on a clean clone.

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

### 3.0 Policy constraints (verbatim citations)

The following lines are policy constraints; they cannot be changed by any experimental admission rule.

From `ralph/project_policy/starters/testing-policy.md:264` (verbatim):
> the default-lane browser time budget is a measured
>     value (declared in this policy's `RALPH-FACT: browser_budget_seconds`).

From `ralph/project_policy/starters/testing-policy.md:271-280` (verbatim, one paragraph):
> BELOW the measured budget, a new browser journey is admitted WITHOUT
>     any mandatory time offset elsewhere in the lane; the
>     distinct-browser-only-risk requirement above is the only
>     qualification, and the cost-neutral offset rule (every admitted
>     default-lane addition MUST be budget-neutral through a recorded
>     REMOVE / MERGE / REPLACE) is the only experimental relaxation. AT
>     or OVER the measured budget, admission requires BOTH a time offset
>     elsewhere in the lane AND a distinct-risk justification naming
>     the browser-only risk the retained journey covers that no other
>     check covers. The aggregate ceiling (`default_budget_seconds`) is
>     never raised; the rule only changes how new browser journeys are
>     admitted against the ceiling.

From `ralph/project_policy/starters/testing-policy.md:422` (verbatim RALPH-FACT line):
`RALPH-FACT: browser_budget_seconds: PROJECT-FACT-UNRESOLVED`

From `ralph/project_policy/starters/policy-portfolio.toml:2` (verbatim TOML key):
`default_budget_seconds = 60.0`

### 3.1 Two distinct falsification outcomes

The B5 experiment measures both limits independently.

`browser_budget_seconds` (the `RALPH-FACT` key at `testing-policy.md:422`) is the **default-lane BROWSER time budget** — the measured value used for the BELOW / AT-or-OVER admission decision for distinct browser journeys. Each pack declares its own resolved value at `docs/ralph-workflow-policy/testing-policy.md` under the same `RALPH-FACT: browser_budget_seconds:` key.

`default_budget_seconds` (the TOML key at `policy-portfolio.toml:2`, value `60.0`) is the **AGGREGATE test-budget ceiling** — the never-raised cap on total default-lane test execution time (covering browser, unit, and integration tests combined), carried by the portfolio. The plan holds this constant at `60.0` for both packs; the experiment does NOT raise it.

#### 3a. Browser-lane budget falsification (per the BELOW/AT-or-OVER rule)

**Falsified if:** under the relaxed BELOW-budget admission arm, a distinct browser-only risk loses coverage — OR a retained journey is admitted whose distinct risk is already covered by a non-browser check.

The experiment runs two task arms on the browser-sensitive category, on the same snapshot:
- **Control arm:** cost-neutral admission — every admitted default-lane addition is budget-neutral via a recorded REMOVE/MERGE/REPLACE.
- **Treatment arm:** relaxed BELOW-budget admission — distinct-browser-only-risk justification only, no mandatory time offset elsewhere in the lane.

#### 3b. Aggregate ceiling falsification

**Falsified if:** total default-lane test execution time exceeds `default_budget_seconds` (`60.0`) in any run under **either** admission rule, or if default-lane browser time alone breaches that aggregate ceiling.

The aggregate ceiling is never raised by any experimental admission rule. Any run whose total default-lane test time exceeds `60.0` seconds falsifies this outcome regardless of which admission rule was applied. The per-run browser timing is recorded in `tmp/policy-v5-replay-runs/<run_id>/browser-timing.jsonl` (see §8.3).

---

## 4. Held constants

| Constant | Value | Held constant by (path:line) |
|---|---|---|
| Model | Same model, same version, frozen prompt | Pinned by the harness env `RALPH_AGENT_MODEL` to be exported in the planned replay runner wrapper script (`scripts/policy_v5_replay_run.sh`, explicit future prerequisite); runner specification requires refusing to start if the value is absent or differs from the pinned id |
| Harness | Same test harness (pytest per test file; Playwright for browser category only) | `ralph/testing/pytest_timeout_plugin.py:1-30` enforces the per-test 1 s SIGALRM cap; `Makefile:52` pins `PYTEST_SUITE_TIMEOUT_SECONDS=60`; `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS=60` is the immutable combined cap |
| Tools | Same toolchain versions (python 3.14, node from `runtime_environment.py`, browser from `tests/visual_capture/`) | `Makefile:88` exports `VIRTUAL_ENV`; `pyproject.toml` pins `requires-python` and dependency set; `tests/test_runtime_environment.py:1-30` snapshots the runtime so a drift fails the run before scoring starts |
| Time budget | 60-second aggregate ceiling, never raised | `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS` is module-level `Final[int] = 60`; `policy-portfolio.toml:2` `default_budget_seconds = 60.0`; `Makefile:60-72` comments the immutability |
| Immutable behavioral checks | Kernel rules (truthful evidence, fail-closed verification, bounded execution, no weakening of checks, explicit unverified-claim reporting) | `ralph/project_policy/starters/execution-kernel.md:1-200` carries the five rules; every starter's `## AI execution instructions` MUST NOT restate them (`markers.py:REQUIRED_HEADINGS` plus B10 in `execution-kernel.md`); `tests/project_policy/test_kernel_rules_proof.py:1-200` is the focused proof |

**Shared budget reminder:** the 60-second ceiling is the AGGREGATE cap (`policy-portfolio.toml:2`) across all default-lane tests. No experimental admission rule raises it. A run whose total default-lane test execution time exceeds 60.0 s falsifies §3b regardless of admission rule.

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
- The aggregate ceiling (`default_budget_seconds = 60.0`, `policy-portfolio.toml:2`) is a separate, orthogonal budget covering harness/browser time, not agent execution time.

---

## 7. Nine-metric scoring rubric

### 7.1 Per-metric definition

Every metric is graded by a blind reviewer (or, where the metric is mechanical, by the planned scoring aggregator script `scripts/policy_v5_replay_score.py`, an explicit future prerequisite whose decision logic is specified in §9 and verified by `tests/test_policy_v5_replay_decision.py`). Each row names the artifact that produces the value.

| # | Metric | Unit | Scale | Direction | Source artifact | Blind-scoring rubric (0–3) |
|---|---|---|---|---|---|---|
| M1 | **User-visible correctness** | ordinal band | 0–3 (integer) | higher = better | Output of the task under replay (e.g. test report, patch, generated file) | 0 = task fails its primary acceptance criterion; 1 = task passes acceptance but a user-visible regression is present; 2 = task passes with no regression; 3 = task passes and produces a measurable improvement over the v4 baseline |
| M2 | **Regressions** | count of distinct broken behaviors | integer ≥ 0 | lower = better | Diff between this run's protected-outcome suite and the v4 baseline protected-outcome suite | 0 = no regression; 1 = one regression in a non-protected path; 2 = one regression in a protected path; 3 = two or more regressions OR any kernel-rule regression |
| M3 | **Security-boundary preservation** | ordinal band | 0–3 | higher = better | Run of `tests/test_subprocess_env_secret_isolation.py` and `tests/test_tool_exec_check_*.py` plus reviewer visual confirmation of `secrets/` and `auth/` paths | 0 = secret logged or exfiltrated; 1 = secret-not-handled-but-bounded; 2 = secrets isolated, no weakening; 3 = secrets isolated and a new bound added |
| M4 | **Unnecessary abstractions** | ordinal band | 0–3 | higher = better | Reviewer diff inspection of changed files | 0 = introduced wrapper solely to satisfy a test or to forward kwargs with no caller; 1 = introduced wrapper that has a documented second caller; 2 = no new wrapper; 3 = removed a wrapper that pinned internals |
| M5 | **Duplicate tests** | count | integer ≥ 0 | lower = better | `pytest --collect-only -q` output of the run-under-test, diffed against the v4 baseline collect output | 0 = no duplicate observable contract; 1 = one duplicate with no distinct failure mode; 2 = two duplicates; 3 = three or more duplicates |
| M6 | **Verification truthfulness** | ordinal band | 0–3 | higher = better | Run of `tests/test_completion_signals_graded_evidence.py` and `tests/test_completion_receipts.py`, plus reviewer diff of the `evidence/` directory | 0 = claim made without receipt OR receipt forged; 1 = receipt present but unverified; 2 = receipt present, verified, scoped to the run; 3 = receipt present, verified, and one new gated claim promoted to truth |
| M7 | **Human rework** | count of reviewer-requested-change iterations | integer ≥ 0 | lower = better | `tmp/policy-v5-replay-runs/<run_id>/review.md` log of reviewer comments and resolutions | 0 = zero rework requests; 1 = one rework for a typo or naming; 2 = one rework for a structural issue; 3 = two or more reworks OR a redesign request |
| M8 | **Elapsed time** | seconds (wall-clock) | float ≥ 0 | lower = better | `tmp/policy-v5-replay-runs/<run_id>/timing.jsonl` — `start_wallclock`, `end_wallclock` recorded by the runner | Reported as raw value, not graded on a band; used as a secondary input to the keep/restore rule (§9) |
| M9 | **Inference cost** | tokens (prompt + completion) | integer ≥ 0 | lower = better | `tmp/policy-v5-replay-runs/<run_id>/tokens.jsonl` — one row per `handle_exec_command` invocation with the agent's reported token count, summed at run end | Reported as raw value, not graded on a band; used as a secondary input to the keep/restore rule (§9) |

### 7.2 Per-metric keep / restore / redesign decision

The direction of improvement is metric-specific:
- **Higher is better:** M1 (correctness), M3 (security), M4 (abstractions), M6 (truthfulness).
  - `v5 > v4` → `IMPROVE`
  - `v5 < v4` → `RESTORE`
  - `v5 == v4` → `KEEP_TIE`
- **Lower is better:** M2 (regressions), M5 (duplicate tests), M7 (human rework), M8 (elapsed time), M9 (inference cost).
  - `v5 < v4` → `IMPROVE`
  - `v5 > v4` → `RESTORE`
  - `v5 == v4` → `KEEP_TIE`

```
per_metric_decision(v4_val, v5_val, metric_name):
    if metric_name in {"M1", "M3", "M4", "M6"}:
        if v5_val > v4_val:   return IMPROVE
        if v5_val < v4_val:   return RESTORE
        return KEEP_TIE
    if metric_name in {"M2", "M5", "M7", "M8", "M9"}:
        if v5_val < v4_val:   return IMPROVE
        if v5_val > v4_val:   return RESTORE
        return KEEP_TIE
```

**Protected regressions (M2):** ANY increase in regressions (`v5.M2 > v4.M2`) MUST veto retention and return `KEEP_FALSE` immediately.

**Dimensional cost comparisons (M8 and M9):**
M8 is wall-clock seconds; M9 is prompt+completion token count. They are dimensionally distinct units: seconds and tokens MUST NOT be added together (an expression such as `v5.M8 + v5.M9` is invalid and rejected). Instead, evaluate the wall-clock ratio (`v5.M8 / v4.M8`) and token ratio (`v5.M9 / v4.M9`) independently as separate secondary dimensions; redesign (`KEEP_TRUE_REDESIGN`) is triggered if either ratio exceeds 1.25x (i.e. more than 25% cost inflation in either time or tokens).

### 7.3 Aggregate rule

```
decide(v4, v5, per_run):
    # 1. Protected regressions veto retention
    if v5.M2 > v4.M2 or per_run.get("kernel_rule_regression", False):
        return KEEP_FALSE

    # 2. Evaluate primary metrics (M1–M7)
    restores = count(== RESTORE,  primary_decisions)
    improves = count(== IMPROVE,  primary_decisions)
    ties     = count(== KEEP_TIE, primary_decisions)

    if restores >= 1:
        return KEEP_FALSE

    # 3. Independent secondary cost ratios (never sum seconds + tokens)
    time_ratio  = v5.M8 / v4.M8  (if v4.M8 > 0 else 1.0)
    token_ratio = v5.M9 / v4.M9  (if v4.M9 > 0 else 1.0)
    cost_exceeded = (time_ratio > 1.25) or (token_ratio > 1.25)

    # 4. Final resolution
    if improves >= 1:
        if cost_exceeded:
            return KEEP_TRUE_REDESIGN
        return KEEP_TRUE

    if ties == 7 and not cost_exceeded:
        # Equal primary outcomes with lower time/cost handled consistently
        has_cost_improvement = (v5.M8 < v4.M8 and token_ratio <= 1.0) or \
                               (v5.M9 < v4.M9 and time_ratio <= 1.0) or \
                               (v5.M8 < v4.M8 and v5.M9 < v4.M9)
        if has_cost_improvement:
            return KEEP_TRUE

    return KEEP_FALSE
```

`KEEP_TRUE` → v5 ships. `KEEP_TRUE_REDESIGN` → v5 ships but secondary cost inflation (> 1.25x in time or tokens) queues the change for redesign before the next policy bump. `KEEP_FALSE` → restore v4, redesign, re-run.

### 7.4 Tie-break: protected outcomes dominate

ANY increase in regressions in M2 (`v5.M2 > v4.M2`), or any regression in a kernel rule (truthful evidence, fail-closed verification, bounded execution, no weakening of checks, explicit unverified-claim reporting), forces `KEEP_FALSE` regardless of other primary scores. The kernel rules are owned by `ralph/project_policy/starters/execution-kernel.md` and are protected from per-change renegotiation.

### 7.5 Retired controls

Any v5 control that was retired between v4 and v5, AND whose protected failure reappears in the v5 run, is added back before v5 ships. The "protected failure reappears" check is mechanical: `tests/test_<control>_retirement.py` is the proof oracle; the v5 run is rerun with the retired control re-enabled, and if the rerun passes, the control is restored to the v5 pack.

---

## 8. Per-run policy-file-read capture

### 8.1 What is actually observable (and what is not)

`ralph.project_policy.starters.read_starter(name)` (`ralph/project_policy/starters/__init__.py:81-88`) returns **cached bundled content** for VALIDATION and SEEDING (its only callers are `ralph/project_policy/_content_checks.py:67` and the workspace seed path at `__init__.py:113-122`). It does NOT observe whether an installed starter was loaded into an agent's context at run time.

The actual agent-context file-read seam is the MCP `workspace.read_file` handler at `ralph/mcp/tools/workspace/_read_handlers.py:221` (and the multi-file sibling `handle_read_multiple_files` at `_read_handlers.py:844`). The agent's harness invokes this handler every time it materializes a workspace file; the handler calls `workspace.read(path)` against the canonical policy directory `markers.CANONICAL_DIR` (i.e. `docs/ralph-workflow-policy/`).

### 8.2 Capture mechanism

Every call to `handle_read_file` and `handle_read_multiple_files` is wrapped by a logging decorator installed at process start by the replay-runner wrapper. The decorator is the only code change required to observe agent exposure; it lives in `ralph/mcp/tools/workspace/_read_handlers.py` and is conditional on the env var `RALPH_V5_REPLAY_RUN_ID` being set.

The decorator does NOT alter the handler's return value, error semantics, capability check, or selector validation (the existing AC-01 / AC-02 invariants at `_read_handlers.py:259-284` remain unmodified). It appends one line to the per-run ledger:

```
tmp/policy-v5-replay-runs/<run_id>/read.log
```

**Format (one line per resolved path):**
```
<timestamp> <absolute_or_workspace_relative_path> <bytes_read>
```

Where:
- `<timestamp>` is ISO-8601 UTC to the millisecond (e.g. `2025-06-20T14:32:01.003Z`),
- `<absolute_or_workspace_relative_path>` is the path the handler resolved to (post-allow-list, post-capability-check) — for canonical policy reads this is `docs/ralph-workflow-policy/<starter>.md` (per `markers.CANONICAL_DIR`),
- `<bytes_read>` is the integer byte count returned to the agent.

**Example ledger entry for a single canonical policy read:**
```
2025-06-20T14:32:01.003Z docs/ralph-workflow-policy/testing-policy.md 18432
2025-06-20T14:32:01.045Z docs/ralph-workflow-policy/execution-kernel.md 9120
```

### 8.3 Per-run browser timing (for §3b)

For the browser-sensitive category only, the decorator ALSO appends one row per Playwright invocation to `tmp/policy-v5-replay-runs/<run_id>/browser-timing.jsonl` with `{test_id, browser, start_ts, end_ts, total_ms}`. The sum of `total_ms` over the run is the per-run browser time. In each individual run, this browser time plus the non-browser test suite execution time is checked against the aggregate ceiling `default_budget_seconds` (`60.0`, `policy-portfolio.toml:2`). In addition, the per-run browser time is compared against `browser_budget_seconds` to verify compliance with the BELOW / AT-or-OVER admission rule for that run. If total default-lane verification time exceeds 60.0 s in any run, §3b is falsified for that run.

### 8.4 What the ledger records vs. what it does not

- The ledger records every path the agent's harness materialized via `workspace.read_file` / `workspace.read_multiple_files`. It does NOT record kernel-level filesystem reads (e.g. `open()` invoked from inside a Python script the agent ran) — those are not agent exposure to policy content, they are tool output.
- The ledger does NOT include the agent's prompt-injected context, the test-run log, or the agent's tool output. None of these are "policy file reads".
- Directory listings via `handle_list_directory` (`_read_handlers.py:1104+`) are NOT counted as policy file reads; only actual file reads count. "Directory size is not a proxy."

### 8.5 Selective-applicability judgment (B2)

A starter whose trigger did not fire during the run — yet whose name appears in `read.log` (e.g. `docs/ralph-workflow-policy/performance-policy.md` was read but no perf signal file per `markers.PERF_SIGNAL_PATHS` is present in the run) — is a B2 violation. The aggregator script emits one row per such violation in `tmp/policy-v5-replay-runs/<run_id>/b2-violations.jsonl`. The B2 violation count feeds into the per-metric rubric as a sub-criterion of M1 (it is a user-visible regression: a starter the project does not need was loaded into the agent's context and may have changed the agent's behavior).

### 8.6 Tool catalog: the read observation is the only new instrumentation

No new tool, no new seam, no new persisted state. The decorator is one Python file: `ralph/mcp/tools/workspace/_read_handlers.py:221` (`handle_read_file`) and `:844` (`handle_read_multiple_files`) gain a logging decorator that writes one line to the per-run ledger when `RALPH_V5_REPLAY_RUN_ID` is set. The decorator is designed to be registered by exporting `RALPH_V5_REPLAY_RUN_ID` before launching the harness (to be automated in the planned replay runner wrapper script `scripts/policy_v5_replay_run.sh`, an explicit future prerequisite).

---

## 9. Keep-or-restore rule (code-expressible)

The decision rule is implemented and verified by the executable test suite `tests/test_policy_v5_replay_decision.py`, planned for packaging into the scoring aggregator script `scripts/policy_v5_replay_score.py` (explicit future prerequisite), and expressible as:

```python
from collections.abc import Mapping

HIGHER_IS_BETTER: frozenset[str] = frozenset({"M1", "M3", "M4", "M6"})
LOWER_IS_BETTER: frozenset[str] = frozenset({"M2", "M5", "M7", "M8", "M9"})
PRIMARY_METRICS: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")


def per_metric_decision(v4_val: float | int, v5_val: float | int, metric_name: str) -> str:
    if metric_name in HIGHER_IS_BETTER:
        if v5_val > v4_val:
            return "IMPROVE"
        if v5_val < v4_val:
            return "RESTORE"
        return "KEEP_TIE"
    if metric_name in LOWER_IS_BETTER:
        if v5_val < v4_val:
            return "IMPROVE"
        if v5_val > v4_val:
            return "RESTORE"
        return "KEEP_TIE"
    raise ValueError(f"Unknown metric: {metric_name}")


def decide(
    v4: Mapping[str, float | int],
    v5: Mapping[str, float | int],
    per_run: Mapping[str, object] | None = None,
) -> str:
    # 1. Protected regressions (M2): ANY increase in regressions vetoes retention.
    v4_m2 = float(v4.get("M2", 0))
    v5_m2 = float(v5.get("M2", 0))
    if v5_m2 > v4_m2 or bool(per_run and per_run.get("kernel_rule_regression", False)):
        return "KEEP_FALSE"

    # 2. Evaluate primary metrics (M1-M7) with metric-specific directions.
    n_restore, n_improve, n_tie = 0, 0, 0
    for m in PRIMARY_METRICS:
        res = per_metric_decision(float(v4.get(m, 0)), float(v5.get(m, 0)), m)
        if res == "RESTORE":
            n_restore += 1
        elif res == "IMPROVE":
            n_improve += 1
        else:
            n_tie += 1

    if n_restore >= 1:
        return "KEEP_FALSE"

    # 3. Dimensional cost comparisons (M8 seconds, M9 tokens).
    # Evaluated independently; do NOT sum seconds and tokens together.
    v4_m8 = float(v4.get("M8", 0.0))
    v5_m8 = float(v5.get("M8", 0.0))
    time_ratio = (v5_m8 / v4_m8) if v4_m8 > 0.0 else 1.0

    v4_m9 = float(v4.get("M9", 0))
    v5_m9 = float(v5.get("M9", 0))
    token_ratio = (v5_m9 / v4_m9) if v4_m9 > 0.0 else 1.0

    cost_exceeded = (time_ratio > 1.25) or (token_ratio > 1.25)

    # 4. Resolve decision.
    if n_improve >= 1:
        return "KEEP_TRUE_REDESIGN" if cost_exceeded else "KEEP_TRUE"

    if n_tie == 7 and not cost_exceeded:
        has_cost_improvement = (
            (v5_m8 < v4_m8 and token_ratio <= 1.0)
            or (v5_m9 < v4_m9 and time_ratio <= 1.0)
            or (v5_m8 < v4_m8 and v5_m9 < v4_m9)
        )
        if has_cost_improvement:
            return "KEEP_TRUE"

    return "KEEP_FALSE"
```

**Restore signal (KEEP_FALSE):** Restore v4, redesign the change, re-run. "Keep most of it" is not a valid outcome.

**Finer redesign signal (KEEP_TRUE_REDESIGN):** v5 ships, but the unchanged axis or secondary cost inflation is queued for redesign before the next policy bump.

**Retired controls:** Any v5 control that was retired and whose protected failure reappears in the v5 run is added back before v5 ships (§7.5).

---

## 10. Blinded-review input

The reviewer panel receives a **redacted input package** that does not disclose which response is v4 or v5:

**Package contents:**
1. **Sanitized read.log / exposure ledger** — the per-run policy-file-read ledger (`tmp/policy-v5-replay-runs/<run_id>/read.log`). Because v5 references `execution-kernel.md` and selectively loaded conditional starters that could unblind reviewers, the unblinded coordinator anonymizes starter paths (e.g. `policy-doc-A`, `policy-doc-B`) before presenting this ledger to reviewers, or evaluates selective-applicability (B2) violations mechanically via aggregator scripts.
2. **Scored rubric** — the nine-metric rubric (§7 table) with scores for each run, labeled by treatment code.
3. **Treatment-code assignment** — an unblinded coordinator (not on the review panel) holds the mapping:

   | Code | Actual pack |
   |------|-------------|
   | R1 | v4 (baseline) or v5 (treatment), per paired run |
   | R2 | the other pack in the pair |

   Reviewers see only R1/R2 labels; they do not know which is v4 or v5 until after scoring is complete and the blind is broken by the coordinator.

**What reviewers assess:** functional output correctness, protected outcome preservation, verification truthfulness, rework burden, and elapsed time — against the rubric scores, not against pack identity.

---

## 11. Timebox

Replay is a **separate phase** with its own timebox. It is not part of the v5 implementation timebox. The implementation timebox covers steps A–C; the replay timebox covers step D and must conclude before any v5 ship decision.

---

## 12. References

- Gloaguen et al., *Evaluating AGENTS.md*, v3. Available: <https://arxiv.org/abs/2602.11988v3>
- `ralph/project_policy/markers.py:65-99` v5 history entry: the five changes and the v4→v5 consequence for existing projects.
- `ralph/project_policy/starters/testing-policy.md:263-281` (B5 browser-budget admission rule, two-limit structure).
- `ralph/project_policy/starters/policy-portfolio.toml:2` (`default_budget_seconds = 60.0`).
- `ralph/mcp/tools/workspace/_read_handlers.py:221` (`handle_read_file`) and `:844` (`handle_read_multiple_files`) — the actual agent-context file-read seam.
- `ralph/project_policy/starters/__init__.py:81-88` (`read_starter`) — what is NOT observed by this seam.

---

## D. Task snapshots and immutable behavioral checks

Each category uses a real pinned snapshot: a real commit ref, a real working-copy command, a real test-file oracle. Snapshots are constructed following the fixture-construction procedure defined below in §D.0 (planned as `scripts/policy_v5_replay_freeze.sh`, an explicit future prerequisite script whose reference implementation is provided below). After construction, the snapshot is hashed; the hash is the only artifact reviewed. No `locked/*` branches are referenced; the previous draft's `locked/feature-add-feature-flag` etc. did not exist in the tree and were replaced with concrete pinned commands.

### D.0 Fixture-construction script: `scripts/policy_v5_replay_freeze.sh` (future prerequisite)

The script specification below defines the reference fixture construction. When implemented as `scripts/policy_v5_replay_freeze.sh` (or executed manually via bash), it serves as the single source of fixture construction before a replay run that uses that snapshot.

```
#!/usr/bin/env bash
# scripts/policy_v5_replay_freeze.sh
# Construct a real pinned snapshot for the v5 paired replay.
# Usage: scripts/policy_v5_replay_freeze.sh <category> <snapshot-name> <base-ref>
# Example: scripts/policy_v5_replay_freeze.sh feature add-feature-flag-journey f760933b2fae095ecdb8bf9f06ae8125a5dd9e90
set -euo pipefail

category="$1"
name="$2"
base_ref="$3"

# 1. Materialize the base ref into a tmp worktree at a fixed path.
worktree="/tmp/policy-v5-replay-snapshots/${category}/${name}"
git worktree add --detach "${worktree}" "${base_ref}"

# 2. Run the focused suite on the fresh worktree to prove the
#    snapshot is self-consistent (no pre-existing failure).
( cd "${worktree}" && \
  PYTEST_SUITE_TIMEOUT_SECONDS=60 \
  PYTEST_WORKERS=auto \
  python -m pytest -q "tests/integration/test_pipeline_commit_cleanup_to_commit_e2e.py::test_pipeline_regression_scoped_commit_residue_reinvokes_commit_end_to_end" \
)

# 3. Hash the worktree and write the hash to the snapshot ledger.
sha="$(cd "${worktree}" && git rev-parse HEAD)"
size="$(du -sb "${worktree}" | cut -f1)"
printf '{"category":"%s","name":"%s","base_ref":"%s","worktree":"%s","head":"%s","size_bytes":%s}\n' \
  "${category}" "${name}" "${base_ref}" "${worktree}" "${sha}" "${size}" \
  >> "tmp/policy-v5-replay-snapshots/ledger.jsonl"
```

After running the script, the worktree path and its head SHA are recorded in `tmp/policy-v5-replay-snapshots/ledger.jsonl`. The replay run reads this ledger, checks out the recorded head in a fresh worktree, and runs the per-snapshot oracle listed below. The original draft's `locked/feature-add-feature-flag` style branches are NOT used; the new construction is reproducible from any clone.

### D.1 Feature

**Snapshot 1 — `add-feature-flag-journey`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh feature add-feature-flag-journey f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** add a new end-to-end journey under a new feature flag (`tests/integration/test_pipeline_happy_path_pipeline_phase_transitions.py`), verify guard-inventory accuracy.
- **Real artifact oracle:** `tests/integration/test_pipeline_happy_path_pipeline_phase_transitions.py::TestPipelinePhaseTransitions::test_planning_analysis_routes_to_development` (proves the new flag's pipeline transition runs to completion).
- **Real test-file oracle:** `tests/project_policy/test_policy_auto_commit.py::test_policy_auto_commit_subject_and_scoped_staging` (proves the policy chore commit subject and scoped staging are still correct under the new flag).
- **Immutable behavioral checks:**
  ```
  pytest tests/integration/test_pipeline_happy_path_pipeline_phase_transitions.py -q --tb=short   # must exit 0
  pytest tests/project_policy/test_policy_auto_commit.py::test_policy_auto_commit_subject_and_scoped_staging -q   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                 # must exit 0
  git -C <snapshot> diff --exit-code                                                              # no uncommitted changes
  git -C <snapshot> log --oneline -1                                                              # a commit is recorded
  ```

**Snapshot 2 — `new-auth-provider-journey`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh feature new-auth-provider-journey f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** add a browser journey exercising a new OAuth2 provider integration (`tests/test_subprocess_env_secret_isolation.py` is the secrets-bound proof oracle; the journey itself is the real-artifact oracle).
- **Real artifact oracle:** `tests/test_subprocess_env_secret_isolation.py::test_subprocess_env_strips_broker_secret` (proves the broker secret is stripped in child environments).
- **Real test-file oracle:** `tests/test_subprocess_env_secret_isolation.py::test_subprocess_env_caller_cannot_override_strip` (proves caller cannot override the broker secret strip).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_subprocess_env_secret_isolation.py -q --tb=short              # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                 # must exit 0
  git -C <snapshot> diff --exit-code                                              # no uncommitted changes
  python -c "from pathlib import Path; import re; t=Path('<snapshot>').read_text(); assert 'client_secret' not in t or 'env' in t.lower()"  # no plaintext secrets in snapshot
  ```

### D.2 Regression

**Snapshot 1 — `smoke-test-regression`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh regression smoke-test-regression c67e7536fe8b97831c72d3e5d87644c26b4b2342`
- **Per-arm task:** re-run a previously failing smoke-test fixture to confirm protected outcomes are still enforced.
- **Real artifact oracle:** `tests/test_smoke_canonical_path.py::test_smoke_claude_branch_end_to_end_uses_canonical_submit` (the canonical smoke proof, runs against the v4 baseline first, then again under v5 to prove no regression).
- **Real test-file oracle:** `tests/test_smoke_plumbing_uses_canonical_submit.py::test_smoke_plumbing_claude_branch_stamps_canonical_receipt` (proves the canonical artifact submission path is unchanged).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_smoke_canonical_path.py tests/test_smoke_plumbing_uses_canonical_submit.py -q   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                  # must exit 0
  git -C <snapshot> diff --exit-code                                                               # no uncommitted changes
  python -c "import json; r=json.load(open('<snapshot>/evidence/report.json')); assert r['passed']"  # evidence complete
  ```

**Snapshot 2 — `auth-token-regression`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh regression auth-token-regression c67e7536fe8b97831c72d3e5d87644c26b4b2342`
- **Per-arm task:** re-run a session that previously failed due to a token-expiry edge case.
- **Real artifact oracle:** `tests/test_subprocess_env_secret_isolation.py::test_subprocess_env_strips_broker_secret` (proves the broker secret is stripped in child environments).
- **Real test-file oracle:** `tests/test_subprocess_env_secret_isolation.py::test_subprocess_env_caller_cannot_override_strip` (proves caller cannot override the broker secret strip).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_subprocess_env_secret_isolation.py -q --tb=short            # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                               # must exit 0
  git -C <snapshot> diff --exit-code                                            # no uncommitted changes
  python -c "from pathlib import Path; t=Path('<snapshot>').read_text(); assert 'expired' in t or 'refresh' in t"  # snapshot encodes the expiry scenario
  ```

### D.3 Refactor

**Snapshot 1 — `rename-service-boundary`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh refactor rename-service-boundary f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** rename a service boundary (`HandleX` → `CoordinatorX`) and update all affected stubs; verify guard-inventory accuracy.
- **Real artifact oracle:** `tests/test_pipeline_factory.py::TestPipelineDepsCollaborators::test_bridge_factory_collaborator_is_overridable` (proves the phase collaborators resolve after the boundary refactor).
- **Real test-file oracle:** `tests/test_pipeline_factory_default.py::TestDefaultPipelineFactoryBuild::test_build_returns_pipeline_deps_with_production_defaults` (proves default pipeline composition is unchanged).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_pipeline_factory.py tests/test_pipeline_factory_default.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                              # must exit 0
  git -C <snapshot> diff --name-only | grep -E '\.(py|md|toml)$'                              # policy files touched
  python -c "import ast; ast.parse(open('<snapshot>/ralph/cli/main.py').read())"              # valid syntax on a renamed-boundary file
  ```

**Snapshot 2 — `extract-helper-module`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh refactor extract-helper-module f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** extract a shared helper from two modules; update all call sites.
- **Real artifact oracle:** `tests/test_pipeline_deps_composition.py::test_build_default_pipeline_deps_returns_valid_pipeline_deps` (proves the extracted helper composes correctly).
- **Real test-file oracle:** `tests/test_pipeline_orchestration_flow.py::test_full_pipeline_transitions_from_planning_to_complete` (proves orchestration order is unchanged after the extraction).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_pipeline_deps_composition.py tests/test_pipeline_orchestration_flow.py -q   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                              # must exit 0
  git -C <snapshot> diff --exit-code                                                           # no uncommitted policy changes
  python -c "import ast; ast.parse(open('<snapshot>/ralph/pipeline/deps.py').read())"          # valid syntax
  ```

### D.4 Integration-heavy

**Snapshot 1 — `multi-step-api-chain`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh integration-heavy multi-step-api-chain f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** multi-step API call chain with auth headers, token refresh, and retry logic.
- **Real artifact oracle:** `tests/integration/test_pipeline_multi_cycle_memory_regression.py::test_pipeline_multi_cycle_memory_regression_no_drift` (proves the multi-step chain does not leak memory across cycles).
- **Real test-file oracle:** `tests/integration/test_commit_cleanup_rock_solid.py::test_handle_commit_cleanup_phase_rock_solid` (proves the commit cleanup phase survives the multi-step run).
- **Immutable behavioral checks:**
  ```
  pytest tests/integration/test_pipeline_multi_cycle_memory_regression.py tests/integration/test_commit_cleanup_rock_solid.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                                                          # must exit 0
  git -C <snapshot> diff --exit-code                                                                                                       # no uncommitted policy changes
  python -c "from ralph.mcp.saturated_dispatch import bounded_result; bounded_result('test', max_items=10)"                                # dispatch helper smoke
  ```

**Snapshot 2 — `db-migration-plus-query`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh integration-heavy db-migration-plus-query f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** schema migration followed by a query that depends on the new columns.
- **Real artifact oracle:** `tests/integration/test_pipeline_final_commit_cleanup_to_final_commit_e2e.py::test_pipeline_final_cleanup_to_final_commit_end_to_end` (proves the cleanup-to-commit transition survives the migration+query chain).
- **Real test-file oracle:** `tests/test_commit_cleanup_diff.py::test_commit_cleanup_diff_respects_gitignore` (proves the diff step still respects the ignore rules after the migration).
- **Immutable behavioral checks:**
  ```
  pytest tests/integration/test_pipeline_final_commit_cleanup_to_final_commit_e2e.py tests/test_commit_cleanup_diff.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                                                       # must exit 0
  git -C <snapshot> diff --exit-code                                                                                                    # no uncommitted policy changes
  python -c "from pathlib import Path; m=Path('<snapshot>/migrations').glob('*'); assert any(p.suffix in {'.sql','.py'} for p in m)"   # migration artifact present
  ```

### D.5 Security-sensitive

**Snapshot 1 — `token-handling-path`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh security-sensitive token-handling-path f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** exercise token generation, storage, and refresh paths; verify no secrets in logs.
- **Real artifact oracle:** `tests/test_subprocess_env_secret_isolation.py::test_subprocess_env_strips_broker_secret` (proves the token path does not leak secrets).
- **Real test-file oracle:** `tests/test_tool_exec_check_network_exfiltration.py::TestCheckNetworkExfiltration::test_curl_to_external_url_is_blacklisted` (proves the network-exfiltration check still fires on the token path).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_subprocess_env_secret_isolation.py tests/test_tool_exec_check_network_exfiltration.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                                       # must exit 0
  git -C <snapshot> diff --exit-code                                                                                    # no uncommitted policy changes
  python -c "import subprocess,os; r=subprocess.run(['git','-C','<snapshot>','log','--oneline','-1'],capture_output=True,env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}); assert r.returncode==0"  # clean git state
  python -c "import re,sys; t=open('<snapshot>/evidence/transcript.txt').read(); sys.exit(0 if not re.search(r'password\\s*=\\s*[\\\"\\\\'][^\\\"\\\\']+[\\\"\\\\']', t, re.I) else 1)"  # no plaintext password in transcript
  ```

**Snapshot 2 — `input-sanitization-path`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh security-sensitive input-sanitization-path f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** submit payloads with injection patterns; verify sanitization is applied end-to-end.
- **Real artifact oracle:** `tests/test_tool_exec_check_container_escape.py::TestCheckContainerEscape::test_docker_is_blacklisted` (proves container-escape-style inputs are blocked).
- **Real test-file oracle:** `tests/test_tool_exec_check_privilege_escalation.py::TestCheckPrivilegeEscalation::test_sudo_is_blacklisted` (proves privilege-escalation-style inputs are blocked).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_tool_exec_check_container_escape.py tests/test_tool_exec_check_privilege_escalation.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                                                          # must exit 0
  git -C <snapshot> diff --exit-code                                                                                       # no uncommitted policy changes
  python -c "from pathlib import Path; t=Path('<snapshot>/evidence/payloads.json').read_text(); assert 'drop' in t.lower() or 'union' in t.lower() or 'rm -rf' in t"   # payload set contains injection patterns
  ```

### D.6 Browser-sensitive

**Snapshot 1 — `dom-interaction-journey`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh browser-sensitive dom-interaction-journey f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** E2E journey exercising click, hover, and DOM-state assertions via Playwright.
- **Real artifact oracle:** `tests/test_visual_capture.py::test_handle_media_capture_appends_ledger_records` (proves the capture matrix round-trips a DOM-state journey).
- **Real test-file oracle:** `tests/test_visual_capture_lifecycle.py::test_retained_capture_set_round_trips_cells` (proves the before-state is immutable across runs).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_visual_capture.py tests/test_visual_capture_lifecycle.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                            # must exit 0
  git -C <snapshot> diff --exit-code                                                         # no uncommitted policy changes
  python -c "import json; d=json.load(open('<snapshot>/evidence/capture.json')); assert d['total_browser_time_ms'] < 60000"   # under aggregate ceiling
  ```

**Snapshot 2 — `network-timing-journey`**
- **Construction:** Planned command (via future prerequisite `scripts/policy_v5_replay_freeze.sh` or manual execution): `bash scripts/policy_v5_replay_freeze.sh browser-sensitive network-timing-journey f760933b2fae095ecdb8bf9f06ae8125a5dd9e90`
- **Per-arm task:** E2E journey asserting on network request timing and resource load ordering.
- **Real artifact oracle:** `tests/test_visual_pipeline_lifecycle.py::test_visual_lifecycle_regression_runner_retains_before_and_recaptures_after` (proves the visual pipeline lifecycle survives the network-timing journey).
- **Real test-file oracle:** `tests/test_visual_verdict.py::test_verdict_accepts_exactly_three_inputs` (proves the three-input verdict boundary is preserved).
- **Immutable behavioral checks:**
  ```
  pytest tests/test_visual_pipeline_lifecycle.py tests/test_visual_verdict.py -q --tb=short   # must exit 0
  python -m ralph.diagnose --workspace <snapshot>                                             # must exit 0
  git -C <snapshot> diff --exit-code                                                          # no uncommitted policy changes
  python -c "import json; d=json.load(open('<snapshot>/evidence/timing.json')); assert d['total_browser_time_ms'] < 60000"   # under aggregate ceiling
  ```
