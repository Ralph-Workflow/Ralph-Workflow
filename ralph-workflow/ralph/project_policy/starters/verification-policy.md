<!-- ralph-policy-schema: v3 -->
<!-- ralph-policy-id: verification-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point for the
project. It enumerates every gate that must pass before code can be
merged or released, the exact commands, the prerequisites, the time
budgets, the lanes a check may legitimately live in, and the
bypass-detection rules.

Two terms are used precisely throughout. A CHECK is any automated
verification this project owns. A GATE is a check in the default lane —
one the authoritative entry point runs on every change. Every gate is a
check; not every check is a gate. Lanes are named, never cited by number
alone, because the testing policy defines its own lanes with a different
ordering.

## Default requirements

* A single authoritative pre-merge verification entry point MUST exist.
  Expensive platform, device, release, security, and scheduled checks MAY
  use named profiles, declared under Gate lanes below, when this policy
  states when each profile is mandatory.
* Gates MUST include, as applicable to the project: tests, type
  checking, linting, formatting checks, policy enforcement scripts, and
  any other mandatory project gate.
* Testing is mandatory for behavior-bearing software. Every language's
  maintained type-checking, linting, and formatting gates are mandatory
  when suitable tools exist. Preference, inconvenience, legacy findings,
  or missing setup does not make a supported gate inapplicable.
* A gate documented here but not actually runnable is non-compliant.
  Documented impossibility MUST be reported as an active blocker.
* Bypass detection MUST use native or existing checks when available.
  Custom tooling is required only when repository risk justifies it; a
  project MUST NOT create a hollow gate solely to satisfy this policy.
* Verification MUST pass in full, with NO exemption for a failure the
  current change did not cause. "It was already broken", "that failure
  is unrelated to my change", and "someone else introduced it" are NOT
  acceptable outcomes: a red gate is a red gate, and whoever next
  observes it owns fixing it. Preventing regressions outranks
  completing the task in hand — if the two conflict, fix the
  regression first and finish the task after.
* Do NOT spend effort establishing WHO caused a failure. Stashing your
  changes, bisecting, or re-running against a clean tree to prove a
  failure is "pre-existing" is almost NEVER useful work: the answer does
  not change what you must do next, which is fix it. Provenance is only
  worth investigating when it is genuinely diagnostic — when knowing the
  triggering change tells you what the bug IS — never to decide whether
  the failure is yours to own. It is always yours to own. Read the
  failure, find the root cause, repair it.
* Every check MUST sit in a declared lane, and every check outside the
  default gate MUST additionally have a declared owner (see Gate lanes).
  A check that runs only in an undeclared opt-in suite the default gate
  excludes will rot unnoticed: give it a lane, or delete it.
* Verification MUST complete in a time proportional to the codebase, and
  the limit MUST be enforced by the gate itself (fail on exceed), never
  by convention. Record it as `verification_time_budget`. Sizing:

  - **Guide (small/medium projects):** roughly **1 second per 1k LOC**.
  - **HARD CAP: 2 minutes**, whatever the size. A verification gate that
    takes longer than ~2 minutes destroys the edit/verify loop that
    both humans and AI agents depend on, so the cap — not the per-LOC
    rate — is the binding constraint for any project past ~120k LOC.
    The wider industry guideline for a commit build is about ten minutes;
    this cap is deliberately far stricter because an AI agent BLOCKS on
    this gate many times per task, so the cost is paid per iteration
    rather than per commit — roughly a five-iteration task reaches the
    ten-minute figure at this cap.
  - **Nesting:** the test suite is ONE STEP inside this budget, not a
    parallel allowance. The testing policy caps the default suite at 60
    seconds precisely so type checking, linting, formatting, and audits
    fit in the remainder. A suite consuming the whole gate budget is a
    breach even when the suite's own limit is satisfied.
  - **Ratchet:** the budget may shrink freely; it may only GROW as a
    deliberate, reviewed change that tracks genuinely-fast new checks.
    A project already comfortably under budget MUST NOT relax up toward
    the guide OR the cap — both are ceilings, never entitlements. Record
    the project's own measured figure, never the cap as a default.

* A run is judged on **time to a correct, verified change**; speed never permits partial proof.
* Prevent an extra pass first; within a pass, the cost of proving the change is the largest term; where they conflict, avoid the extra pass.
* The four commitments are: **fast path first**, **a slow gate is a defect**, **do not leave the loop slower**, and **checks answer consistently**. Non-compliance is a defect: fix it within the requested run when that fits its budget, or raise it to an owner who can act. A change is never done on a partial check.
* **Fast path first.** Maintain a narrow check that catches the change's
  likely failures and run it before the full gate. The fast path MUST be
  SCOPED TO THE CHANGE — derived from the modified files and the
  TRANSITIVE set of things that depend on them — not a hand-picked fixed
  subset that drifts out of relevance. When the full gate already
  completes inside the fast-path cap, THE FULL GATE IS THE FAST PATH and
  no selection mechanism is required; small projects satisfy this rule by
  being fast, not by building machinery.
* Any selection mechanism MUST FAIL SAFE — when impact cannot be
  determined it runs everything, never less. Fall back to the full set
  whenever any of the following is in play, because each one carries a
  dependency edge that file-level analysis cannot see: reflection,
  dynamic dispatch, dependency-injection containers, plugin or
  entry-point registries; monkeypatching by string path; shared test
  fixtures, factories, or per-directory test configuration, which apply
  by location rather than by import; database migrations; code-generation
  inputs such as schemas, interface definitions, and templates; runtime
  resource files such as SQL, HTML/template, i18n, and static assets;
  environment variables and feature flags, which change behaviour with NO
  file change at all; cross-language boundaries and generated client
  types; deletions and renames, where the importers that just broke are
  exactly what must run; lockfiles and transitive dependency bumps; and
  configuration or build files. This list is a floor, not a ceiling: an
  unmapped input is a reason to run everything.
* Selection is an optimization of the FAST PATH ONLY. The full gate before
  done runs everything in its lane; a change is never declared verified on
  a selected subset alone.
* Fast-path and full-gate costs MUST be measured from invocation to
  answer, including startup, and the gate MUST emit its observed duration
  so a regression is visible as a number rather than a feeling. Target 10
  seconds for the fast path, HARD CAP 30 seconds. Startup alone exceeding
  the target is a defect to fix, not a reason to widen the target; record
  the measured startup floor if it is the binding constraint. Added
  verification cost and any budget breach require an explicit decision and
  actionable escalation.
* Parallel execution and result caching are the two legitimate levers for
  holding the budget as the project grows, and both are permitted only
  when they cannot produce a FALSE GREEN. Parallelism requires checks that
  are independent and order-insensitive. Caching requires a key covering
  EVERY input that can change the answer — use the same taxonomy as the
  fail-safe list above, since a cache key that omits an environment
  variable, a generated source, a lockfile, or a resource file returns
  green for a run that never happened. A key MUST additionally cover the
  inputs that are not repository content at all: the toolchain and
  interpreter versions, the operating system and architecture, and the
  invocation flags — each changes a check's answer while leaving every
  tracked file identical. Caching MUST also provide a documented bypass. Unlike selection, caching has no full-gate backstop, so an
  incomplete key is a correctness defect, not a latency one. An agent
  reporting a change verified MUST disclose when the result was a cache
  hit rather than a fresh run.
* A check that answers inconsistently is a defect: fix or raise it, never
  normalize reruns. Failure output MUST identify what broke so diagnosis
  is bounded.
* Orientation that every run would otherwise rebuild MUST be durable,
  cheap to read, and updated by the run that learns it.
* Phase order, wait permissions, and phase-granted capabilities belong to
  workflow machinery outside this policy; the runtime owns their settings
  and reports them alongside phase time where available.
* A slow gate is a DEFECT, not a cost of doing business. Verification
  time that grows superlinearly, or a step that hangs, is a HARD
  indicator of a real problem — most often an architectural one, and the
  testing policy's performance rule diagnoses it in full. Treat a
  performance regression in the gate with the same seriousness as a
  failing test: diagnose the coupling and fix the design. NEVER raise a
  budget to make a slow gate fit.
* The full gate remains required before done. Reducing recurring
  full-gate, fast-path, or setup cost is in scope when it fits the run
  budget without displacing the request; otherwise raise it where an owner
  can act. A raised cost must be actionable, not merely reported.

## Gate lanes

Every check this project owns MUST sit in exactly one of the lanes below,
or in a lane declared by another policy in this set (the testing policy's
human-review and one-off-evidence lanes are the common cases). The lane
system keeps the default gate FAST and TRUSTED without pushing real checks
into an unowned opt-in suite where they decay silently.

1. DEFAULT GATE — the authoritative pre-merge entry point. Mandatory on
   every change, bound by the time budget above, and required to be
   deterministic and hermetic enough to run from a clean clone. A check
   that CAN meet those constraints MUST be in this lane, whether it is new
   or existing. Budget pressure is never a qualification for another lane:
   a check that is merely slow is a defect in this lane, under "a slow
   gate is a DEFECT" above.
2. NAMED PROFILE — a check that is genuinely valuable and repeatable but
   CANNOT meet the default gate's constraints: it needs a live
   third-party service, a paid or rate-limited API, a device or platform
   matrix, fixed hardware for a timing measurement, a large dataset, a
   long soak, a release artifact, or output that is non-deterministic by
   nature and can only be scored against a recorded baseline rather than
   asserted. This list is EXHAUSTIVE; a new category requires amending
   this policy. Dataset size and device coverage qualify only when the
   SCALE or the DEVICE is itself the behaviour under test. A profile is legitimate only
   when all five of these are declared here: its command, its OWNER, its
   TRIGGER or SCHEDULE, the fact that it FAILS HARD when it runs, and the
   DATE IT LAST RAN together with the staleness horizon past which it is
   treated as decayed. A profile past its horizon with no run evidence is
   not a profile — it belongs in the DELETED lane, because a check that
   never runs reports a safety it never verified.
   The categories above are the single authoritative list for this project.
   The testing policy routes into this lane rather than restating them, so
   a category added here is available there automatically.
3. DELETED — the correct destination for a check with no owner, no
   trigger, no lane, or no evidence of ever running. Deleting a decayed
   check is honest; leaving it in an unobserved corner is not.

A check MUST NOT be moved into the named-profile lane to escape a red
result or a budget breach, and a new check MUST NOT be born there for the
same reason. Demotion is a design decision with an owner, recorded with
its reason; it is never a way to make the gate green today. The declared
owner MUST be a named person or team, not a role the author occupies by
default.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
`none` IS a legal resolved value when the project genuinely has no such
mechanism: write it plainly with the reason, for example
`gate_parallelism_mechanism: none — gate steps run serially`. Do NOT invent
a deferral for something that will never arrive. For a small project whose
full gate already fits the fast-path cap, record
`fast_path_selection_mechanism: none — full gate is the fast path`.
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires.
Record `verification_time_budget` as the project's OWN measured figure plus
small headroom, never the 2-minute cap as a default — the ratchet then
holds that tighter number.
The whole-file placeholder scan rejects unresolved-work markers anywhere in
the file, including inside ordinary prose: the all-caps four-letter "to do"
marker, the "to be determined" abbreviation, the "fix me" marker, and a
double opening brace. Do not write any of them, even to describe them.
Then delete this comment. -->

RALPH-FACT: authoritative_verify_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_prerequisites: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_order: PROJECT-FACT-UNRESOLVED
RALPH-FACT: fast_path_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: fast_path_selection_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_parallelism_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_cache_mechanism_and_key: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_duration_report_location: PROJECT-FACT-UNRESOLVED
RALPH-FACT: bypass_detection_lint_audit: PROJECT-FACT-UNRESOLVED
RALPH-FACT: bypass_detection_typecheck_audit: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_integration_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: required_verification_profiles: PROJECT-FACT-UNRESOLVED
RALPH-FACT: verification_time_budget: PROJECT-FACT-UNRESOLVED
RALPH-FACT: verification_time_enforcement_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_lane_review_cadence_and_owner: PROJECT-FACT-UNRESOLVED

`required_verification_profiles` is the single home for the project's named
non-default profiles, including any declared by the testing policy. Record
all five declared fields for each one: its command, its named owner, its
trigger or schedule, the fact that it fails hard when it runs, and the date
it last ran together with its staleness horizon.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* ENSURE every gate listed here is actually runnable in the
  environment. Document any gate that cannot run and the reason.
* RUN the change-scoped fast path first, then the full authoritative
  gate before declaring the change done. A selected subset is never
  sufficient proof on its own.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run, and disclose any result served from
  a cache rather than a fresh run.
* RECORD the observed full-gate duration when it changes materially, and
  justify any verification cost the change adds.
* PLACE any new check in a declared lane, and say which lane by name.
  Outside the default gate, name its owner too. A check with no lane is
  not shipped.
* UPDATE this policy in the same workflow that changes the authoritative
  entry point, gate order, selection or caching mechanism, profile set,
  or bypass-detection audit.

An agent MUST NOT:

* Add a "verification" command that does not exercise every default-gate
  check.
* Weaken a gate to obtain a passing result.
* Move a check into a profile, create a new check in a profile, or narrow
  the selected subset, to avoid a red result or a budget breach.
* Rely on a cache or a selection mechanism that can report success
  without having proven the change.
* Hide bypasses via file-level disables or blanket silencers.
* Dismiss, defer, or excuse a failing gate on the grounds that the
  failure is pre-existing, unrelated to the current change, or someone
  else's regression. Fix it, or stop and report it as an active
  blocker — never report work as verified while any gate is red.
* Stash, bisect, or re-run against a clean tree merely to establish that
  a failure is pre-existing. The verdict is the same either way — fix
  it. Spend that effort on the root cause instead.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet is recorded as a
RALPH-PENDING deferral — `RALPH-PENDING: <approved-tool> (assumed <date>);
review trigger: <trigger>` — which reaches readiness and is resolved by a
later dev cycle when its trigger fires; only a gate that truly cannot EVER
exist is recorded as inapplicable with a reason and the condition that
would create it.
For a project small enough that the full gate already finishes inside the
fast-path cap, record `fast_path_command` as the full gate command itself
and `fast_path_selection_mechanism` as `none — full gate is the fast path`.
Record the real command and confirm it EXISTS (you MAY run it once as a
bounded probe to check that it resolves). Do NOT fix failing checks and do
NOT run a suite to green; a failing or slow gate is the project's problem
to address later. Run only the commands you declare here. Then delete this
comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review that every declared profile has run within its staleness horizon, that no check has been demoted out of the default gate to avoid a red result, and that the recorded gate duration still matches reality; evidence: dated gate-lane review listing each profile with its last-run date; owner: the named person or team recorded in the gate_lane_review_cadence_and_owner fact

The expected successful result is exit 0 from the authoritative entry
point, within the recorded time budget. On failure, report the failing
gate and the failure category (assertion, type, lint, policy, timeout,
environmental). A `timeout` category is a HARD failure: fix the slow
check, never the budget.

The gate-lane review recorded above runs on the cadence in
`gate_lane_review_cadence_and_owner`, and a review older than that cadence
is itself a finding — without it, the staleness horizon that keeps a
declared profile honest has nothing enforcing it.

## Bypass detection

Lint and typecheck bypass detection MUST be enforced as part of the
authoritative verification gate. The bypass-detection rules:

* Newly weakened global configuration (per-file-ignores,
  ignore_missing_imports, follow_imports = silent, ignore_errors,
  disable_error_code, etc.) is detected and reported.
* Blanket or unexplained inline suppressions are detected and reported.
* Commands that claim to verify the project while omitting required
  paths are detected and reported.
* A selection or caching mechanism that can shrink what actually ran is
  treated as a bypass surface: it MUST be observable in the gate output
  (what was selected, what was cached) so a shrinking gate cannot pass
  as a passing gate.

<!-- REPLACE-ME: set the real bypass-audit command. Most ecosystems have a
native way to do this — a lint rule that bans blanket suppressions, a
type-checker flag that reports unused or unexplained ignores — so prefer
wiring that over writing new tooling. This section is checked on its own
and accepts ONLY a runnable `RALPH-COMMAND:` line whose first token is an
approved gate tool: an inapplicable declaration and a pending deferral are
both rejected here, whatever is permitted elsewhere in this file. If no
such command exists yet, wire the smallest real one rather than declaring
an exemption. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 (no bypass detected). On
failure, report the affected file, line, and bypass category. Approved
documented exceptions MUST be listed under "Exceptions" below.

## Exceptions

A documented bypass (e.g. a generated file with a `// @ts-nocheck`
header) requires a documented rationale, scope, owner, and removal or
review date. Undocumented bypasses are non-compliant.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new gate is added or an existing gate is removed.
* The authoritative entry point changes.
* The fast-path command or its change-scoping mechanism changes.
* The gate's parallelism or caching mechanism changes.
* A named profile is added, retired, or has its owner or trigger changed.
* A named profile has not run within its declared staleness horizon.
* The observed gate duration moves materially, or the time budget changes.
* The bypass-detection audit changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Speed of Code Reviews"
  http: https://google.github.io/eng-practices/review/reviewer/speed.html
  review date: 2026-07-11

* publisher: Google SRE Book
  title: "Monitoring Distributed Systems"
  http: https://sre.google/sre-book/monitoring-distributed-systems/
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Continuous Integration"
  http: https://martinfowler.com/articles/continuousIntegration.html
  review date: 2026-07-11

* publisher: DORA / Google Cloud
  title: "Capabilities: Continuous integration"
  http: https://dora.dev/capabilities/continuous-integration/
  review date: 2026-08-08

* publisher: Google Research
  title: "Taming Google-Scale Continuous Testing"
  http: https://research.google/pubs/taming-google-scale-continuous-testing/
  review date: 2026-08-08

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: verification-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v3 -->`
