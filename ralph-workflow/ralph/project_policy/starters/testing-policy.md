<!-- ralph-policy-schema: v3 -->
<!-- ralph-policy-id: testing-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Testing Policy

## Purpose and scope

This policy governs how agents plan, write, run, and maintain automated
tests — and which acceptance criteria MUST NOT become tests at all. It
applies to every change that adds, modifies, or removes behaviour that
could regress without a test.

Automated coverage is one of four verification lanes. Human judgment,
scheduled checks against real resources, and one-off probes are real
verification work, but they are not this suite's work: Suite admission
routes each criterion to its lane. Misrouting a criterion INTO the default
suite is a violation, not surplus diligence.

Cite lanes BY NAME, never by number — the verification policy numbers its
own lanes differently.

## Default requirements

Each rule is MANDATORY unless it says SHOULD. Where two genuinely cannot
both hold, record the conflict and the choice on the change, and order what
gets DEFERRED: coverage last, then suite mass, then the time budget. This
orders what waits, never what breaks; it never authorizes raising a time
limit. Lawful moves are in Test optimization and retirement, re-routing via
Suite admission, and fixing the production seam.

1. Assert stable observable contracts at the narrowest useful boundary.
   Public-surface, contract, package, and unit tests are all valid when they
   express behavior cheaply without coupling to incidental internals.
2. Prefer narrow unit tests for pure functions, parsers, validators, and
   decision tables where every branch is reachable from the signature alone.
3. Refactor the production boundary only when a test reveals a real
   cohesion, dependency, or I/O-seam problem. Never invent a public API
   solely to accommodate a test.
4. Automated testing is mandatory for first-party behavior that Suite
   admission routes to the automated lane. A missing framework or
   hard-to-test code requires a real seam and gate; it does not make testing
   inapplicable. Another lane is not an exemption — the criterion is
   verified there, with its own recorded evidence and owner.
5. Tests MUST be deterministic. A test that can pass or fail without a
   behavior change is a defect: fix it, or quarantine it under rule 27.
   Removal is governed by rule 29. Never retry until green, and never
   configure the runner to retry for you.
6. Unit tests MUST isolate every ambient dependency behind an in-memory
   fake: time and timezone, filesystem, network, subprocess, randomness,
   global singleton mutation. Integration, contract, system, and end-to-end
   tests MAY use controlled real resources where that interaction is the
   behavior under test; those MUST be isolated, reproducible, bounded, and
   cleaned up.
7. Mock real I/O by default — it is the dominant source of slow, flaky
   tests. Real external resources are the EXCEPTION, permitted only at the
   layers where that interaction is the behavior under test. Within a test,
   prefer a REAL implementation when it is fast, deterministic, and simply
   wired; otherwise prefer a fake upholding the real contract over a stub
   hardcoding returns, and either over asserting how a collaborator was
   called.
8. Every suite MUST enforce a runner-level time limit — per-test and/or
   whole-suite — that FAILS the gate when exceeded. A suite with no enforced
   limit is itself a violation, not a slow-but-tolerable suite: agents block
   on this pipeline, so one unbounded test hangs a run indefinitely.
   Convention and reviewer vigilance do not count.
9. The suite budget is DRAWN FROM the gate budget, not parallel to it. The
   suite is one step among type checking, linting, formatting, and audits,
   so record a POINT VALUE strictly below the project's recorded
   `verification_time_budget` — never a range, never that budget in full.
   Guide: ~0.5s per 1k LOC, deliberately half the verification policy's
   whole-gate guide, leaving the remainder for other steps. HARD CAP 60
   seconds. Past ~120k LOC the cap binds, and it does not move. A recorded
   gate budget tighter than 120s binds before either number here. These
   limits govern the DEFAULT SUITE; named profiles and quarantine carry
   their own budgets.
10. A timeout is a HARD failure, never a route to green.
    - Per-test: raising it is almost always wrong. Repair the test, usually
      by mocking its I/O (rule 7).
    - Suite: may SHRINK freely. It grows only as a reviewed change tracking
      more genuinely-fast tests that satisfies rule 16, and only once
      measured runtime crosses 80% of the current budget with every test
      still fast. A suite under budget MUST NOT relax toward the guide or
      cap: both are ceilings, never entitlements.
11. A PERFORMANCE failure is a HARD failure, at least as serious as a
    functional one: a functional failure is one broken behavior, a slow or
    hanging suite is usually a broken ARCHITECTURE that keeps producing
    bugs. The usual root cause is a missing seam — production code that
    cannot run without real I/O, subprocesses, sleeps, network, or agents,
    which is the signature of tests bound to internals instead of driving
    the system as a black box. Add the seam. Do NOT raise the timeout,
    split the suite to dodge the budget, or skip/xfail to get green.
12. Every bug fix to behaviour in the automated lane MUST add a regression
    test that fails on the bug and passes on the fix; the name SHOULD encode
    the regression. A fix routed to another lane records its regression
    evidence THERE — never nowhere.
13. Every new behaviour in the automated lane MUST add positive coverage.
    Negative coverage is mandatory where rejection, failure, permission,
    boundary, or recovery behavior exists. Other lanes record evidence there.
14. Shape the suite so the cheapest, most isolated layers carry the most
    cases: many fast tests at the lowest layer a behavior supports, fewer
    integration tests across a seam, a thin cap of end-to-end. The ratio is
    stack-dependent, not a quota — code decomposing into pure units leans to
    unit tests, UI-heavy code shifts toward component and integration tests.
    Invariant across every stack: broad and cheap at the base, narrow and
    expensive at the top.
15. Keep case enumeration OUT of slow layers. Exhaustive branch, edge,
    boundary, and negative coverage (rule 13) belongs at the unit or
    component layer. Integration tests cover contracts and failure modes
    across a seam that unit tests cannot see. End-to-end tests are the
    SCARCEST resource: a few critical journeys plus the failure paths whose
    breakage would be catastrophic — never the place to enumerate variations.
16. ADMISSION: every new test MUST name the distinct failure it catches that
    no existing test catches, name the nearest existing test found, and
    state the search performed. To decline a test as already covered, name
    that test by file and test name — an unnamed claim is not a reason.
    WHEN UNCERTAIN, ADD THE TEST and flag it for the suite review: an
    uncertain duplicate is far cheaper than an uncovered behaviour. This
    rule stops redundant mass; it never justifies writing less.
    A parametrised sweep over a documented domain satisfies this rule
    COLLECTIVELY — name the failure the sweep catches, not one per case —
    and counts as ONE unit of mass under rule 26 however many cases the
    runner reports, so a compliant sweep cannot manufacture a mass finding.
17. Tests MUST be order-independent and parallel-safe. No test may depend on
    another having run, on runner order, or on a sibling's leftovers. Shared
    mutable state, fixed ports, fixed temp paths, shared working
    directories, and unguarded global or environment mutation are DEFECTS.
    Parallelism is the primary lever for holding the budget as the suite
    grows; one order-coupled test forfeits it for the whole suite.
18. Tests MUST NOT depend on ambient properties the project does not pin:
    locale, timezone, encoding, filesystem case-sensitivity or path
    separator, CPU count, memory, word size, shell and PATH. Pin the value
    in the test rather than inheriting the machine's. A test that passes
    only on its author's machine is a defect however reliably it passes
    there. Where the DEVICE or platform is itself the behavior under test,
    route to a named profile.
19. Tests MUST be structure-insensitive and behavioral: assert what the unit
    does, not how it is built. Asserting private internals, call counts and
    order, log text, or exact message wording is forbidden UNLESS that
    string is a contract published BY THIS PROJECT — then cite in the test
    where it is published. A third party's wording is never such a contract.
    A test needing edits during a behavior-preserving refactor is a defect.
20. A test MUST be able to fail. Zero-assertion tests, assertions behind a
    skippable conditional, a constant asserted against itself, and
    assert-no-exception where the contract is a value are DEFECTS. Before
    admitting a test, confirm it fails when the behavior is broken — the
    purpose of the WRITE instruction below. Where that instruction's
    exemption applies, demonstrate falsifiability by mutating the artifact
    instead; the exemption is from ORDERING, never from falsifiability.
21. A test MUST NOT assert what a static gate already proves. Type
    conformance, lint, formatting, and schema validity belong to the
    type-checking and linting policies; re-asserting them at runtime is pure
    mass. This never reaches first-party code whose runtime JOB is to
    validate, parse, reject, or serialize — exercising a parser is testing
    behaviour, and such a test is never removable under this rule.
22. Absolute wall-clock duration, throughput, frame rate, and memory
    footprint are NEVER default-suite assertions: hardware-, load-, and
    cache-dependent, they are flaky everywhere and meaningful nowhere. Route
    them to a named profile on declared hardware, compared against a
    recorded baseline rather than a constant. This governs assertions INSIDE
    tests, not the runner-enforced timeouts of rules 8-10, which are budget
    gates; a red timeout is never excused by citing this rule.
23. Generative and property-based tests MUST run from a PINNED seed
    committed to the repository — printing a fresh random seed satisfies
    neither this rule nor rule 5. Randomized exploration belongs in a named
    profile. Any counterexample it shrinks MUST be committed as a fixed
    regression test, with or without an accompanying fix. Assertions over
    non-deterministic generated output, including model output, do not
    belong in the default suite; route them to a profile scored against a
    threshold.
24. Snapshot, golden-file, and approval tests are BOUNDED tools, for
    genuinely large structural artifacts only. Each snapshot MUST be small
    enough that its diff is reviewable, MUST be committed so that diff
    appears in the change under review, and MUST NOT be regenerated in the
    change that made its gate red. A snapshot nobody reads is a change
    detector, not coverage.
25. Coverage percentage is a FLOOR, never the target: a line can execute
    without its consequences being asserted, so a high number is fully
    compatible with a suite that asserts nothing. Treat it as a floor, not a
    ceiling to aim at. Where the stack supports it, check assertion quality
    directly — mutation analysis over CHANGED LINES is the strongest signal
    and is bounded enough to run per change — and never raise a coverage
    threshold as a substitute.
26. SUITE MASS is governed, not free. Record `suite_test_count_command` and
    review the trend at the suite review. A suite growing faster than the
    distinct failures it catches is degrading even while every test stays
    fast and green, because a test costs on every run, refactor, and review
    — not only in seconds. Compare against rule 25's signal or the
    distinct-failure count from the last review; resolve growth by
    retirement, not absorption.
27. FLAKE QUARANTINE is a named, bounded, owned lane, never a synonym for
    skipping. A non-deterministic test MAY be quarantined so it stops
    blocking merges, only under all of: recorded with an owner and an expiry
    date; still run and reported where an owner sees it; fixed or DELETED at
    that expiry. The expiry MUST be short — a week is the common default —
    and MUST NOT be extended; a second quarantine of the same test is a
    deletion. Quarantine MUST be bounded by a recorded maximum size; at that
    maximum nothing further may be quarantined until the backlog clears.
    Quarantined tests run outside the DEFAULT SUITE's budget so a hanging
    test cannot block merges — but never unbounded: they carry their own
    runner-enforced timeout recorded in
    `quarantine_mechanism_expiry_and_max_size`, because rule 8 admits no
    suite without a limit. Deleting a test at its expiry is the ONE removal
    exempt from rule 29, and requires citing the quarantine record, owner,
    and expiry date.
28. The suite MUST run to completion from a clean clone on a supported
    machine, using only the documented setup and secret mechanism. A test
    needing a credential a contributor cannot obtain that way, a
    hand-provisioned account, or a resource that will not exist next quarter
    does not belong in the default suite — route it per Suite admission.
    This governs the DEFAULT SUITE; a named profile exists precisely because
    it cannot meet this bar.
29. REMOVAL IS NEVER A ROUTE TO GREEN. A failing test MUST NOT be deleted,
    un-collected, or otherwise removed. Removing a test requires observing
    it GREEN immediately beforehand and RECORDING that run — the command and
    its actual output, dated, on the change — and the removing change MUST
    NOT be the change that made it red. When a test fails, the only lawful
    moves are fixing the production code or fixing the test, and "fixing the
    test" never means weakening an assertion so a later change can retire it
    as vacuous. Lawful grounds are enumerated in Test optimization and
    retirement; the sole exemption is a quarantine expiry under rule 27.

## Suite admission

Route each acceptance criterion to one lane — or to a named destination
recorded with an owner — and record the routing where the change is
reviewed. Forcing a criterion into the suite is how suites grow large,
slow, and untrusted.

1. AUTOMATED TEST — the default. Use when the criterion is objective,
   machine-decidable, expressible as a stable observable contract, and
   reproducible from a clean clone (rule 28). Most behavior lands here. A
   criterion that could land here MUST NOT be routed elsewhere;
   inconvenience is never a routing reason, and if the obstacle yields to a
   fake, a smaller fixture, or a seam (rules 6-7), this lane wins.
   A criterion measured against a PUBLISHED EXTERNAL STANDARD — a contrast
   ratio, an RFC, a wire format, a schema — belongs here even when the
   surrounding judgment is perceptual: the threshold is stable across
   redesigns, so only the taste question goes to review.
2. HUMAN REVIEW — use when the criterion is perceptual, aesthetic,
   ergonomic, or editorial with no published standard behind it: visual
   balance, whether wording reads in the product's voice, whether a layout
   feels crowded, whether an error message actually helps. These are
   judgments, not assertions; encoding one as a constant yields a test that
   fails on every legitimate redesign and never on a genuinely bad result.
   Manual exploratory verification belongs here. Route to the
   `RALPH-REVIEW:` line of the owning policy, or to THIS policy's line when
   no policy in the project owns it. Automation MAY supply evidence for the
   judgment; it MUST NOT supply the verdict.
3. NAMED PROFILE — use when the check is repeatable and worth keeping but
   cannot meet the default suite's constraints. Qualifying categories are
   declared ONCE, in the verification policy's Gate lanes, and are not
   restated here so the two cannot drift. Routing here is invalid unless the
   same change also adds the profile as a runnable command and records it
   there with ALL FIVE fields — command, named owner, trigger or schedule,
   fails-hard-when-run, and last-run date with staleness horizon. An
   unrunnable or undeclared profile is an unrouted criterion.
4. ONE-OFF EVIDENCE — use when the verification is not repeatable and is not
   meant to be: an expiring credential, a sandbox to be torn down, a
   one-time migration, a capacity probe, a spike. State what makes it
   unrepeatable in a way still true next quarter. Perform it, record the
   dated command and actual output as evidence on the change, and do NOT
   commit it as a test. A check that cannot pass six months from now on a
   clean clone is a receipt, not a test — and filing a receipt in the suite
   makes it a scheduled failure a future agent will "fix" by deleting the
   assertion.

Three binding consequences:

* Any check whose verdict can change WITHOUT a change in this repository is
  not a default-suite test: a third party's uptime, a new security
  advisory, a package index, a certificate authority, DNS. Vendor liveness
  is a monitoring question — route it to the project's operational alerting
  with a named alert and owner. Whether our client handles the vendor's
  documented responses and failures is the automated lane against a fake,
  with a contract test in the named-profile lane to detect drift.
* When two lanes both seem to apply, the properties of the CHECK decide, not
  the convenience of the credential. A rate-limited API is a named profile
  even when the key is obtainable locally.
* No lane other than the automated one means unverified. Each carries its
  own owner and DATED evidence recorded on the change — the command and
  actual output where a command ran, the reviewer and verdict where a
  judgment was made. A criterion with no lane, no owner, and no record is a
  blocker.

## Test optimization and retirement

Optimize before changing limits, preserving the clearest test for every
contract:

1. Delete TDD scaffolding once a clearer test pins the same behavior; never
   delete the sole coverage for a live contract.
2. Collapse an integration case to a unit test when a seam expresses the
   same observable contract more cheaply.
3. Retire an end-to-end test once a deterministic integration test covers
   the same contract; keep the smallest higher-layer test for boundaries the
   lower layer cannot observe.
4. Restore parallelism by fixing order-coupling (rule 17), not by pinning
   the offending tests to serial execution.

Retirement is maintenance, never a route to green — rule 29 governs, so a
test is removed only from observed and recorded green. Subject to that, a
test MUST be removed when any of the following holds, and removal needs the
same evidence as addition — name what is no longer covered and why that is
correct:

* It asserts nothing, or its assertions cannot fail (rule 20).
* It duplicates a cheaper test's observable contract and catches no distinct
  failure of its own (rule 16).
* It pins internals, call sequences, or wording that is not a contract
  published by this project (rule 19).
* It asserts what a static gate already proves (rule 21).
* It pins a third party's behavior rather than this project's handling of it.
* It passes only under an unpinned locale, timezone, encoding, CPU count, or
  filesystem semantics (rule 18).
* Its fixture expired: an embedded date, certificate, token, or vendor
  snapshot no longer reflecting reality. Regenerate against a controlled
  clock, or retire it.
* The behaviour it covers is unreachable from any production entry point —
  delete the code, then the test.
* It passed its quarantine expiry without a fix (rule 27).
* The behavior it covered has been deleted.

A test deleted on these grounds is not lost coverage and MUST NOT be
reinstated to raise a count or percentage.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
`none` IS a legal resolved value when the project genuinely has no such
mechanism: write it plainly with the reason, for example
`parallel_execution_mechanism: none — suite runs serially`. Do NOT invent a
deferral for something that will never arrive.
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires.
The whole-file placeholder scan rejects unresolved-work markers anywhere in
the file, including inside ordinary prose: the all-caps four-letter "to do"
marker, the "to be determined" abbreviation, the "fix me" marker, and a
double opening brace. Do not write any of them, even to describe them.
Then delete this comment. -->

RALPH-FACT: test_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_command_prerequisites: PROJECT-FACT-UNRESOLVED
RALPH-FACT: primary_test_framework: PROJECT-FACT-UNRESOLVED
RALPH-FACT: secondary_test_frameworks: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_isolation_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: io_mocking_approach: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suite_time_budget: PROJECT-FACT-UNRESOLVED
RALPH-FACT: per_test_timeout: PROJECT-FACT-UNRESOLVED
RALPH-FACT: timeout_enforcement_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: parallel_execution_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: slow_test_report_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suite_test_count_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: assertion_quality_check: PROJECT-FACT-UNRESOLVED
RALPH-FACT: supported_platform_matrix: PROJECT-FACT-UNRESOLVED
RALPH-FACT: external_dependency_test_approach: PROJECT-FACT-UNRESOLVED
RALPH-FACT: clean_clone_setup_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: flake_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: quarantine_mechanism_expiry_and_max_size: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suite_review_cadence_and_owner: PROJECT-FACT-UNRESOLVED
RALPH-FACT: regression_test_convention: PROJECT-FACT-UNRESOLVED

Three prerequisite facts exist and are NOT interchangeable:
`clean_clone_setup_command` is the one-time bootstrap taking a fresh clone
to a runnable state (rule 28); `test_command_prerequisites` records only
what `test_command` needs BEYOND that bootstrap, and is `none` when the
bootstrap suffices; the verification policy's `gate_prerequisites` covers
the whole gate, of which the suite is one step.

Named non-default profiles are recorded once, in the verification policy's
`required_verification_profiles`. Do not duplicate that list here.

## AI execution instructions

Work this order on every change. Each step names the rule that governs it;
follow the rule, do not re-derive it.

1. ROUTE every acceptance criterion through Suite admission, and state each
   lane by name, before writing any test.
2. WRITE the test before the production change and report the observed red
   result. If it unexpectedly passes, confirm existing behavior and refine
   the missing contract. The one exemption is a test over a static data file
   or generated artifact where no production code executes: record why, and
   demonstrate falsifiability by mutating the artifact instead (rule 20).
   Never manufacture a failure.
3. NAME the distinct failure, the nearest existing test, and the search
   performed (rule 16). When uncertain, add the test.
4. ISOLATE: mock real I/O and pin every ambient dependency the test does not
   own — clock, timezone, locale, encoding, seed (rules 6, 7, 18, 23).
5. PREFER existing helpers, fixtures, and utilities; do not add a testing
   dependency the existing stack can express.
6. RETIRE superseded coverage in the same change, only from recorded green,
   saying what went and why (rules 29 and Test optimization and retirement).
7. RUN every `RALPH-COMMAND:` gate below and report the actual outcome.
   Never report a command that was not run.
8. UPDATE this policy when the test command, framework, isolation strategy,
   mocking approach, parallelism mechanism, profile set, or budget changes.

An agent MUST NOT:

* Default to white-box tests coupled to private internals (rule 19).
* Assert a subjective judgment — visual balance, tone, "feels right" — as a
  machine constant instead of routing it to human review.
* Commit a check depending on an expiring credential, a temporary sandbox,
  or a hand-provisioned resource as a default-suite test (rule 28).
* Add a test catching no failure an existing test catches, or that cannot
  fail at all (rules 16, 20).
* Take ANY action whose effect is that a previously-executing assertion no
  longer runs or no longer fails. This includes, and is not limited to:
  skipping, xfail, conditional skipif, deselection by name or marker,
  un-collection, renaming a file out of collection, DELETING THE TEST,
  runner-level rerun or retry configuration, reducing parallelism to mask
  order-coupling, lowering a coverage threshold, and raising, disabling, or
  removing a time limit. Retirement is the only lawful removal, and only
  from green (rule 29).
* Treat quarantine as a destination; its expiry is never extended (rule 27).
* Introduce wall-clock sleeps or uncontrolled external I/O.

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
would create it. The gate MUST enforce the suite time limit recorded above,
via the runner's timeout rather than a manual stopwatch.
Record the real gate command and confirm it EXISTS and enforces a timeout
(you MAY run it once as a bounded probe, capped at ~10s, to check that it
resolves). Do NOT fix failing tests or run the suite to green — a failing or
slow suite is the project's problem to address later. Run only the commands
you declare here. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review the suite for tests that catch no distinct failure, tests that cannot fail, criteria misrouted into automation instead of human review, order-coupled tests, expired fixtures, and quarantined tests past expiry; evidence: dated suite review recording the test count, the distinct-failure count this suite can catch, tests admitted, tests flagged as uncertain duplicates under rule 16, tests retired with the green run that authorized each removal, rule conflicts recorded since the last review, and quarantine status; owner: the named person or team recorded in the suite_review_cadence_and_owner fact

Expected result: a deterministic suite finishing inside its enforced
budget. On failure report the failing test names and the category —
assertion, collection error, timeout, environmental. A `timeout` is a HARD
failure: fix the slow test (rules 7, 10), never the budget.

The suite review above certifies the judgment no runner can make: whether
each test still earns its place. Rules 16, 19, 20, 21, 26 and Suite
admission depend on it, so it runs on the cadence in
`suite_review_cadence_and_owner`, and a review older than that cadence is
itself a finding. A green gate does not substitute for it — a suite of
worthless tests passes every gate this policy can automate.

## Exceptions

A narrower scope (e.g. no negative tests for purely declarative YAML
schemas) requires a documented rationale in this section, the scope of
the exception, and the owner of the exception. Exceptions expire at the
next policy review; an expired exception without an updated rationale
is treated as a violation.

## Maintenance triggers

Review this policy in the same workflow as any of:

* The test framework, runner, or command changes.
* A new test layer is introduced, or a named profile is added or retired.
* The isolation strategy, mocking approach, or fake-injection pattern
  changes.
* The suite budget, per-test timeout, timeout enforcement, or parallel
  execution mechanism changes.
* The verification policy's gate budget changes — the suite budget is drawn
  from it, so a change there restates the ceiling here.
* The count from `suite_test_count_command` grows without a matching growth
  in the distinct failures the suite catches.
* A test is quarantined, or a quarantined test reaches its expiry.
* The recorded suite review is older than its cadence.
* Coverage thresholds, assertion-quality checks, or other quality bars
  change.
* The supported platform matrix changes.
* A test dependency is added or replaced.

## Research basis

* publisher: Google Testing Blog / Google Engineering Practices
  title: "Just Say No to More End-to-End Tests"
  http: https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html
  review date: 2026-07-11

* publisher: Google Testing Blog
  title: "Flaky Tests At Google and How We Mitigate Them"
  http: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Test Pyramid"
  http: https://martinfowler.com/bliki/TestPyramid.html
  review date: 2026-07-11

* publisher: Kent C. Dodds
  title: "The Testing Trophy and Testing Classifications"
  http: https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications
  review date: 2026-07-14

* publisher: Pearson / Kent Beck
  title: "Test-Driven Development: By Example"
  http: https://www.pearson.com/en-us/subject-catalog/p/test-driven-development-by-example/P200000009421/9780321146533
  review date: 2026-07-12

* publisher: Kent Beck
  title: "Test Desiderata"
  http: https://testdesiderata.com/
  review date: 2026-08-08

* publisher: O'Reilly / Software Engineering at Google
  title: "Testing Overview"
  http: https://abseil.io/resources/swe-book/html/ch11.html
  review date: 2026-08-08

* publisher: O'Reilly / Software Engineering at Google
  title: "Test Doubles"
  http: https://abseil.io/resources/swe-book/html/ch13.html
  review date: 2026-08-08

* publisher: Martin Fowler
  title: "Eradicating Non-Determinism in Tests"
  http: https://martinfowler.com/articles/nonDeterminism.html
  review date: 2026-08-08

* publisher: Google Research
  title: "State of Mutation Testing at Google"
  http: https://research.google/pubs/state-of-mutation-testing-at-google/
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

* Policy id: `<!-- ralph-policy-id: testing-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v3 -->`

## Visual design evidence

An appearance assertion (CSS/class/style/DOM) is NOT evidence of design quality. Design proof requires captures graded visually via the criterion 8 verdict.

Criterion 8 verdict authority: a capture-backed criterion 8 verdict is agent-produced evidence and does not close the design review lane; the named human review verdict remains required.

Appearance assertions may protect implementation details and accessibility behaviour, but they must not be submitted as visual design proof. Tests and development results must identify the capture set and the visual verdict that supports a UI plan item.

RALPH-FACT: visual_verdict_artifact: design_verdict
RALPH-FACT: visual_capture_handle: ralph://media/{artifact_id}
RALPH-FACT: design_capture_command: <declared capture command from the design-system policy>
