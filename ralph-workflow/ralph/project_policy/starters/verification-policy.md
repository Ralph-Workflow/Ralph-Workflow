<!-- ralph-policy-schema: v3 -->
<!-- ralph-policy-id: verification-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point: every gate
that must pass before merge or release, the exact commands, the
prerequisites, the time budgets, the lanes a check may live in, and the
bypass-detection rules.

Two terms are used precisely. A CHECK is any automated verification this
project owns. A GATE is a check in the default lane, run by the
authoritative entry point on every change. Every gate is a check; not every
check is a gate. Cite lanes BY NAME — the testing policy numbers its lanes
differently.

## Default requirements

1. A single authoritative pre-merge entry point MUST exist. Expensive
   platform, device, release, security, and scheduled checks MAY use named
   profiles declared under Gate lanes, when this policy states when each is
   mandatory.
2. Gates MUST include, as applicable: tests, type checking, linting,
   formatting checks, policy enforcement scripts, and any other mandatory
   project gate.
3. Testing is mandatory for behavior-bearing software. Every language's
   maintained type-checking, linting, and formatting gates are mandatory
   when suitable tools exist. Preference, inconvenience, legacy findings,
   or missing setup does not make a supported gate inapplicable.
4. A gate documented here but not runnable is non-compliant. Documented
   impossibility MUST be reported as an active blocker.
5. Bypass detection MUST use native or existing checks when available.
   Custom tooling is required only when repository risk justifies it; never
   create a hollow gate solely to satisfy this policy.
6. Verification MUST pass in full, with NO exemption for a failure the
   current change did not cause. "It was already broken", "unrelated to my
   change", and "someone else introduced it" are NOT acceptable outcomes: a
   red gate is a red gate, and whoever next observes it owns fixing it.
   Preventing regressions outranks completing the task in hand — if the two
   conflict, fix the regression first and finish the task after.
7. Do NOT spend effort establishing WHO caused a failure. Stashing,
   bisecting, or re-running against a clean tree to prove a failure
   pre-existing is almost NEVER useful: the answer does not change what you
   must do next, which is fix it. Provenance is worth investigating only
   when genuinely diagnostic — when the triggering change tells you what the
   bug IS — never to decide whether the failure is yours to own. It always
   is.
8. Every check MUST sit in a declared lane, and every check outside the
   default gate MUST additionally have a declared owner (see Gate lanes). A
   check running only in an undeclared opt-in suite the default gate
   excludes will rot unnoticed: give it a lane, or delete it.
9. Verification MUST complete in time proportional to the codebase, enforced
   by the gate itself (fail on exceed), never by convention. Record it as
   `verification_time_budget`.
   - **Guide:** roughly **1 second per 1k LOC**.
   - **HARD CAP: 2 minutes**, whatever the size — a gate slower than that
     destroys the edit/verify loop humans and agents depend on, so past
     ~120k LOC the cap, not the rate, binds. The wider industry guideline
     for a commit build is about ten minutes; this cap is far stricter
     because an agent BLOCKS on this gate many times per task, paying the
     cost per iteration rather than per commit — a five-iteration task
     reaches ten minutes at this cap.
   - **Nesting:** the suite is ONE STEP inside this budget, not a parallel
     allowance. The testing policy caps the default suite at 60 seconds so
     type checking, linting, formatting, and audits fit in the remainder. A
     suite consuming the whole gate budget is a breach even when its own
     limit is satisfied.
   - **Ratchet:** the budget may shrink freely; it grows only as a reviewed
     change tracking genuinely-fast new checks. A project under budget MUST
     NOT relax toward the guide OR the cap — both are ceilings, never
     entitlements. Record the project's measured figure, never the cap.
10. A run is judged on **time to a correct, verified change**; speed never permits partial proof.
11. Prevent an extra pass first; within a pass, the cost of proving the change is the largest term; where they conflict, avoid the extra pass.
12. The four commitments are: **fast path first**, **a slow gate is a defect**, **do not leave the loop slower**, and **checks answer consistently**. Non-compliance is a defect: fix it within the requested run when that fits its budget, or raise it to an owner who can act. A change is never done on a partial check.
13. FAST PATH FIRST. Maintain a narrow check catching the change's likely
    failures and run it before the full gate. It MUST be SCOPED TO THE
    CHANGE — the modified files and the TRANSITIVE set depending on them —
    not a hand-picked subset that drifts out of relevance. When the full
    gate already finishes inside the fast-path cap, THE FULL GATE IS THE
    FAST PATH and no selection mechanism is required; small projects satisfy
    this by being fast, not by building machinery.
14. Any selection mechanism MUST FAIL SAFE: when impact cannot be
    determined it runs everything, never less. Fall back to the full set
    whenever any of these is in play, because each carries a dependency edge
    file-level analysis cannot see — reflection, dynamic dispatch, DI
    containers, plugin or entry-point registries; monkeypatching by string
    path; shared fixtures, factories, or per-directory test configuration,
    which apply by location rather than import; database migrations;
    code-generation inputs such as schemas, interface definitions, and
    templates; runtime resource files such as SQL, templates, i18n, and
    static assets; environment variables and feature flags, which change
    behaviour with NO file change; cross-language boundaries and generated
    client types; deletions and renames, where the importers that just broke
    are exactly what must run; lockfiles and transitive bumps; and
    configuration or build files. This list is a floor: an unmapped input is
    a reason to run everything.
15. Selection optimizes the FAST PATH ONLY. The full gate before done runs
    everything in its lane; a change is never verified on a selected subset.
16. Fast-path and full-gate costs MUST be measured invocation-to-answer
    including startup, and the gate MUST emit its observed duration so a
    regression is a number, not a feeling. Target 10 seconds for the fast
    path, HARD CAP 30 seconds. Startup alone exceeding the target is a
    defect to fix, not a reason to widen it; record the measured startup
    floor when it binds. Added cost and any budget breach require an
    explicit decision and actionable escalation.
17. Parallelism and caching are the two legitimate levers for holding the
    budget as the project grows, permitted only when they cannot produce a
    FALSE GREEN. Parallelism requires independent, order-insensitive checks.
    Caching requires a key covering EVERY input that can change the answer —
    the same taxonomy as rule 14, since a key omitting an environment
    variable, generated source, lockfile, or resource file returns green for
    a run that never happened — PLUS the inputs that are not repository
    content at all: toolchain and interpreter versions, operating system and
    architecture, and invocation flags. Caching MUST also provide a
    documented bypass. Unlike selection, caching has no full-gate backstop,
    so an incomplete key is a correctness defect, not a latency one.
18. A check answering inconsistently is a defect: fix or raise it, never
    normalize reruns. Failure output MUST identify what broke so diagnosis
    is bounded.
19. Orientation every run would otherwise rebuild MUST be durable, cheap to
    read, and updated by the run that learns it.
20. Phase order, wait permissions, and phase-granted capabilities belong to
    workflow machinery outside this policy; the runtime owns their settings
    and reports them alongside phase time where available.
21. A SLOW GATE IS A DEFECT, not a cost of doing business. Superlinear
    growth or a hanging step is a hard indicator of a real problem, usually
    architectural — the testing policy's performance rule diagnoses it in
    full. Treat a gate performance regression as seriously as a failing
    test: fix the design. NEVER raise a budget to make a slow gate fit.
22. The full gate remains required before done. Reducing recurring
    full-gate, fast-path, or setup cost is in scope when it fits the run
    budget without displacing the request; otherwise raise it where an owner
    can act. A raised cost must be actionable, not merely reported.

## Gate lanes

Every check MUST sit in exactly one lane below, or in a lane declared by
another policy in this set (the testing policy's human-review and
one-off-evidence lanes are the common cases). Lanes keep the default gate
FAST and TRUSTED without pushing real checks into an unowned opt-in suite
where they decay silently.

1. DEFAULT GATE — the authoritative pre-merge entry point. Mandatory on
   every change, bound by rule 9, and deterministic and hermetic enough to
   run from a clean clone. A check that CAN meet those constraints MUST be
   here, new or existing. Budget pressure never qualifies a check for
   another lane: merely slow is a defect here, under rule 21.
2. NAMED PROFILE — a valuable, repeatable check that CANNOT meet the default
   gate's constraints: a live third-party service, a paid or rate-limited
   API, a device or platform matrix, fixed hardware for a timing
   measurement, a large dataset, a long soak, a release artifact, or output
   non-deterministic by nature that can only be scored against a recorded
   baseline. This list is EXHAUSTIVE; a new category requires amending this
   policy. Dataset size and device coverage qualify only when the SCALE or
   DEVICE is itself the behaviour under test.
   A profile is legitimate only with all five declared here: its command,
   its OWNER, its TRIGGER or SCHEDULE, that it FAILS HARD when it runs, and
   the DATE IT LAST RAN with the staleness horizon past which it is decayed.
   A profile past its horizon with no run evidence belongs in the DELETED
   lane — a check that never runs reports a safety it never verified.
   These categories are the single authoritative list; the testing policy
   routes into this lane rather than restating them, so a category added
   here is available there automatically.
3. DELETED — for a check with no owner, no trigger, no lane, or no evidence
   of ever running. Deleting a decayed check is honest; leaving it in an
   unobserved corner is not.

A check MUST NOT be moved into the named-profile lane to escape a red result
or a budget breach, and a new check MUST NOT be born there for the same
reason. Demotion is a design decision with an owner and a recorded reason,
never a way to make the gate green today. The declared owner MUST be a named
person or team, not a role the author occupies by default.

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
`fast_path_selection_mechanism: none — full gate is the fast path` and
`fast_path_command` as the full gate command itself.
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
all five fields for each: command, named owner, trigger or schedule,
fails-hard-when-run, and last-run date with staleness horizon.

## AI execution instructions

On every change:

1. RUN the change-scoped fast path first, then the full authoritative gate
   before declaring done. A selected subset is never sufficient proof
   (rules 13, 15).
2. ENSURE every gate listed here is runnable in the environment. Document
   any that cannot run, and why.
3. RUN every `RALPH-COMMAND:` gate below and report the actual outcome.
   Never report a command that was not run, and disclose any result served
   from a cache rather than a fresh run (rule 17).
4. RECORD the observed full-gate duration when it changes materially, and
   justify any verification cost the change adds (rule 16).
5. PLACE any new check in a declared lane, naming the lane; outside the
   default gate, name its owner too. A check with no lane is not shipped.
6. UPDATE this policy when the entry point, gate order, selection or caching
   mechanism, profile set, or bypass-detection audit changes.

An agent MUST NOT:

* Add a "verification" command that does not exercise every default-gate
  check.
* Weaken a gate to obtain a passing result.
* Move a check into a profile, create a new check in a profile, or narrow
  the selected subset, to avoid a red result or a budget breach.
* Rely on a cache or selection mechanism that can report success without
  having proven the change.
* Hide bypasses via file-level disables or blanket silencers.
* Dismiss, defer, or excuse a failing gate as pre-existing, unrelated, or
  someone else's regression. Fix it, or stop and report it as an active
  blocker — never report work as verified while any gate is red (rule 6).
* Stash, bisect, or re-run against a clean tree merely to establish that a
  failure is pre-existing. The verdict is the same either way (rule 7).

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
Record the real command and confirm it EXISTS (you MAY run it once as a
bounded probe to check that it resolves). Do NOT fix failing checks and do
NOT run a suite to green; a failing or slow gate is the project's problem
to address later. Run only the commands you declare here. Then delete this
comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review that every declared profile has run within its staleness horizon, that no check has been demoted out of the default gate to avoid a red result, and that the recorded gate duration still matches reality; evidence: dated gate-lane review listing each profile with its last-run date; owner: the named person or team recorded in the gate_lane_review_cadence_and_owner fact

Expected result: exit 0 from the authoritative entry point within the
recorded budget. On failure report the failing gate and the category —
assertion, type, lint, policy, timeout, environmental. A `timeout` is a HARD
failure: fix the slow check, never the budget.

The gate-lane review runs on the cadence in
`gate_lane_review_cadence_and_owner`, and a review older than that cadence
is itself a finding — without it, nothing enforces the staleness horizon
that keeps a declared profile honest.

## Bypass detection

Lint and typecheck bypass detection MUST be part of the authoritative gate:

* Newly weakened global configuration (per-file-ignores,
  ignore_missing_imports, follow_imports = silent, ignore_errors,
  disable_error_code, and similar) is detected and reported.
* Blanket or unexplained inline suppressions are detected and reported.
* Commands claiming to verify the project while omitting required paths are
  detected and reported.
* A selection or caching mechanism that can shrink what actually ran is a
  bypass surface: it MUST be observable in gate output — what was selected,
  what was cached — so a shrinking gate cannot pass as a passing gate.

<!-- REPLACE-ME: set the real bypass-audit command. Most ecosystems have a
native way to do this — a lint rule banning blanket suppressions, a
type-checker flag reporting unused or unexplained ignores — so prefer
wiring that over writing new tooling. This section is checked on its own
and accepts ONLY a runnable `RALPH-COMMAND:` line whose first token is an
approved gate tool: an inapplicable declaration and a pending deferral are
both rejected here, whatever is permitted elsewhere in this file. If no
such command exists yet, wire the smallest real one rather than declaring
an exemption. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

Expected result: exit 0 (no bypass detected). On failure report the affected
file, line, and bypass category. Approved documented exceptions MUST be
listed under "Exceptions".

## Exceptions

A documented bypass (e.g. a generated file with a `// @ts-nocheck`
header) requires a documented rationale, scope, owner, and removal or
review date. Undocumented bypasses are non-compliant.

## Maintenance triggers

Review this policy in the same workflow as any of:

* A gate is added or removed.
* The authoritative entry point changes.
* The fast-path command or its change-scoping mechanism changes.
* The gate's parallelism or caching mechanism changes.
* A named profile is added, retired, or has its owner or trigger changed.
* A named profile has not run within its declared staleness horizon.
* The observed gate duration moves materially, or the budget changes.
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
