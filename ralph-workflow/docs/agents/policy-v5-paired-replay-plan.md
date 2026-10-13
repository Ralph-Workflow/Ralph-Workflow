# Paired-replay measurement plan: v4 vs. v5 shipped policy

## Why this plan exists

The v5 revision reframes the shipped policy as a small outcome-oriented
execution kernel plus evidence-backed, selectively applicable controls.
The static acceptance criteria prove the change was MADE; they do not
prove the change HELPED. This plan is the script for the paired
replay that scores the v5 revision against the v4 baseline on
verifiable run outcomes, with reviewers blind to treatment.

The plan is invoked only after v5 ships (`SCHEMA_VERSION == "v5"`,
every shipped starter carries the v5 marker, the v5 history entry
exists, `make verify` passes). The replay is a separate
operational phase with its own timebox.

## Task set (balanced)

The replay runs the same balanced task set on every paired
replicate, balanced across the five categories that the v4
research (Expeditions-Core) flagged as the ones policy reliably
moved and ones it failed to move:

| Category | Example | What it stresses |
|---|---|---|
| **Feature** | add a public API endpoint with tests | selective policy loading; kernel rule 1 (truthful evidence); guard inventory on the touched domain starter |
| **Regression** | a failing test that captures a real bug | kernel rule 1; cheapest-layer regression rule (testing starter) |
| **Refactor** | extract a function without changing behavior | narrowest-boundary rule (testing); clean-code vs. architecture contradiction pair (B11) |
| **Integration-heavy** | wire a new dependency, add a subprocess call | dependency starter; process / resource ownership (architecture starter) |
| **Security-sensitive** | add a privileged operation, a tenant-scope boundary, a destructive op | security starter; authorization / tenant / scope rules; destructive-operation safety (A2) |

The set is fixed: every replicate sees the same five tasks in the
same order with the same starting tree (the pre-change snapshot).

## Held constants

| Constant | Value | Notes |
|---|---|---|
| Model | the project's recorded runtime model | recorded on the run ledger |
| Harness | the project-pipeline harness | unchanged across runs |
| Tools | the recorded gate executables (make / pytest / mypy / ruff / etc.) | unchanged |
| Time budget | 60 s per test suite, full gate inside the run's budget | unchanged |
| Immutable behavioral checks | the project's pre-declared behavior oracle | unchanged; the oracle is the recorded public-surface artifact per run |

Any deviation on a held constant makes that run's data a
regression test, not a paired-replay sample, and the run is
replaced.

## Comparison: v4 vs. v5

The replay runs each task set on:

1. **v4 baseline** — the shipped v4 policy pack (the pre-change
   snapshot, restored from a tagged commit or a captured snapshot
   fixture; never recapture pixels as the baseline).
2. **v5 treatment** — the shipped v5 policy pack (the current
   pack, with the kernel plus selectively loaded controls).

The same task set runs on both, with reviewers blind to which
pack is in effect at scoring time.

## Run count

The replay needs at least **3 runs per pack per category** to
make the per-category comparison meaningful (n >= 3 is the floor
for paired-sample significance; more runs are encouraged when the
timebox allows). The minimum total is 3 * 5 * 2 = **30 runs**;
the recommended floor is **5 runs per pack per category** (50
runs) when the project has the budget.

The runs are independent; a run that crashes is replaced, not
counted.

## Blind scoring rubric

Reviewers are blind to treatment. Each run is scored on the
following dimensions, each rated 0 / 1 / 2 (blocker / major /
minor):

| Dimension | 0 (blocker) | 1 (major) | 2 (minor) |
|---|---|---|---|
| **User-visible correctness** | the change broke a behavior the oracle would have caught | the change made a behavior different in a way that needs a follow-up | the change is correct |
| **Regressions** | a previously-green test is now red, or a red test is now green-but-shouldn't-be | a previously-absent test is now vacuous | no regression |
| **Security-boundary preservation** | a privilege or scope was lost | a privilege or scope was implicit but not recorded | boundaries preserved and recorded |
| **Unnecessary abstractions** | the change invented a public API solely to satisfy a test | the change added a wrapper solely to satisfy a structural rule | no abstraction invention |
| **Duplicate tests** | the change added a test that catches no distinct failure | the change added a sweep that re-enumerates existing coverage | no duplicates |
| **Verification truthfulness** | the change claimed a green gate that did not run | a recorded outcome is missing a fact the rubric asks for | every claim backed by a record |
| **Human rework** | the change needed a follow-up commit to fix | the change needed a clarification message to merge | merged cleanly |
| **Elapsed time** | the change exceeded the budget | the change ate > 80% of the budget | comfortably under budget |
| **Inference cost** | the change made a far-more-expensive model call than the rubric expects | the change made a measurable unnecessary call | no extra cost |

The rubric score is the sum of the per-dimension scores. A
higher sum is better.

### Human-review rubric for the claim-scope residual

The deterministic substring predicate (`_check_claim_scope` and the
guard-inventory equivalent) covers the cases a literal
substring match can prove. The residual semantic gap — a row
whose `protected_outcome` does not contain a broad phrase but
*implies* one — is scored in a separate human-review item:

* 0: the row's `protected_outcome` overstates the detector's
  observation in a way a substring predicate cannot catch
  (e.g. "policy is internally consistent" when the detector
  reports "validator returned zero findings").
* 1: the row's `protected_outcome` is closer to a detector
  observation than a world-claim, but the wording still
  smuggles in a judgment the detector cannot make.
* 2: the row's `protected_outcome` is exactly what the detector
  reports.

The claim-scope human-review score is reported PER RUN, PER
CONTROL ROW that the v5 pack carries. The aggregated score is a
per-run average.

## Per-run file-read logging

Every run records which policy files it actually read during the
task (the kernel + the loaded conditional starters, not the
shipped set). The log is appended to the run ledger, not committed
to the change; directory size is NOT exposure. The selective-
applicability contract is judged against this log: a run that
loaded a conditional starter whose trigger did not fire is a
violation of B2.

## Keep-or-restore rule

The v5 revision is kept only if the v5 mean rubric score across
all categories is at least equal to the v4 mean (no
regression), and at least one dimension improves without
worsening any other. A v5 result that scores lower than v4
on the mean is a redesign signal, not a deployment signal:
restore v4, redesign the failing dimension, re-run the
replay. A v5 result that scores equal on the mean but improves
one dimension and worsens another is a finer redesign signal:
the worsened dimension names the kernel rule to revisit
before shipping the change.

Restored controls (a control retired in v5 whose protected
failure reappears in the replay) are added back to the policy
or absorbed by a kernel rule before v5 ships. The replay
either keeps the v5 revision or restores a specific control;
"keep most of it" is not a valid outcome.

## Timebox

The replay is bounded: 5 runs per pack per category is the
default; 3 runs is the floor; more than 10 runs is rarely
informative when the rubric stabilizes early. The full replay
is a separate phase with its own timebox; it is not part of
the v5 implementation timebox.

## References

* Gloaguen et al., "Evaluating AGENTS.md" (v3):
  https://arxiv.org/abs/2602.11988v3 — supports unique,
  non-duplicated instructions; does NOT support deleting
  policy wholesale or claiming shorter is always better.
* Martin Fowler, "TestDesiderata":
  https://testdesiderata.com/ — the basis for the kernel rule
  set (truthful evidence, fail-closed verification, bounded
  execution).
* The v5 history entry in `ralph/project_policy/markers.py` —
  what changed in v5, the consequence for an existing v4
  project, and the rationale.
