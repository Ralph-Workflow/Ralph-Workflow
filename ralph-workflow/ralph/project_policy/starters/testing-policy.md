<!-- ralph-policy-schema: v3 -->
<!-- ralph-policy-id: testing-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Testing Policy

## Purpose and scope

This policy governs how every AI agent working in this project plans,
writes, runs, and maintains automated tests — and, equally, which
acceptance criteria MUST NOT become automated tests at all. It applies
to every change that adds, modifies, or removes behaviour that could
regress without a test.

Automated coverage is one of four verification lanes. Human judgment,
scheduled checks against real resources, and one-off probes are real
verification work, but they are NOT this suite's work: the Suite
admission section routes each criterion to the lane that owns it.
Misrouting a criterion into the default suite is a policy violation in
its own right, not a harmless surplus of diligence.

Lanes are referred to BY NAME throughout this policy — the automated
lane, the human-review lane, the named-profile lane, the one-off-evidence
lane. The verification policy defines its own lanes with the same naming
convention; never cite a lane by number alone, because the numbering is
local to each document.

## Default requirements

The numbered rules below are the concrete, enforceable obligations of this
policy. Each is MANDATORY unless it says SHOULD.

Where two rules genuinely cannot both be satisfied, do NOT pick silently.
Record the conflict and the choice on the change, and order what gets
DEFERRED by this precedence: correctness of coverage is deferred last,
then suite mass, then the time budget. This orders which obligation waits
and is recorded — it NEVER licenses breaking a rule. In particular it does
not authorize raising, disabling, or removing a time limit; the lawful
expressions are the moves in Test optimization and retirement, re-routing
through Suite admission, and fixing the production seam. Raise the
conflict at the next suite review.

1. Assert stable observable contracts at the narrowest useful boundary.
   Public-surface, contract, package, and internal unit tests are all valid
   when they express behavior cheaply without coupling to incidental
   implementation details.
2. Prefer narrow unit tests for pure functions, parsers, validators, and
   decision tables where every branch is reachable from the function's
   signature alone.
3. Refactor the production boundary only when a test reveals a real
   cohesion, dependency, or I/O-seam problem. Do NOT invent a public API
   solely to accommodate a test.
4. Automated testing is mandatory for first-party software behavior that
   the Suite admission section routes to the automated lane. A missing
   framework or code that is difficult to test requires a real test seam
   and gate; it does not make testing inapplicable. A criterion routed to
   another lane is not an exemption from verification — it is verified
   there, with its own recorded evidence and owner.
5. Tests MUST be deterministic. A test that can pass or fail without a
   change in behavior is a defect: fix it, or quarantine it under rule 27.
   Removal is governed by rule 29 and the Test optimization and retirement
   section — this rule never authorizes deleting a red test. Never retry it
   until it goes green, and never configure the runner to retry it for you.
6. Unit tests MUST isolate every ambient dependency behind an in-memory
   fake: real time and timezone, filesystem, network, subprocess, random
   number generation, and global singleton mutation. Integration,
   contract, system, and end-to-end tests MAY use controlled real
   resources when that interaction is the behavior under test; those
   resources MUST be isolated, reproducible, bounded, and cleaned up.
7. Mock real I/O by default. Real I/O is the dominant source of slow, flaky
   tests, so mocking, faking, or stubbing filesystem, network/HTTP,
   database, subprocess, and clock access is STRONGLY PREFERRED at the unit
   layer. Touching a real external resource is the EXCEPTION, permitted
   only at the integration, contract, system, or end-to-end layers where
   that specific interaction is the behavior under test. Within a test,
   prefer a REAL implementation when it is fast, deterministic, and has
   simple dependencies; otherwise prefer a fake that upholds the real
   contract over a stub that hardcodes returns, and prefer either over
   asserting on how a collaborator was called.
8. Every test suite MUST enforce a bounded execution time limit, and the
   suite in the main verification pipeline MUST enforce one. This is NOT
   OPTIONAL: a suite with no enforced limit is itself a policy violation,
   not a slow-but-tolerable suite. The limit MUST be enforced by the test
   runner — a per-test and/or whole-suite timeout that FAILS the gate when
   exceeded — never by convention or reviewer vigilance. AI agents block on
   this pipeline, so one unbounded test can hang an entire run indefinitely.
9. The suite budget is DRAWN FROM the verification gate's budget, not
   independent of it. The suite is one step among type checking, linting,
   formatting, and audits, so it MUST be recorded as a POINT VALUE strictly
   below the project's recorded `verification_time_budget` — never a range,
   and never that budget in full. Sizing guide: roughly 0.5 seconds per 1k
   LOC, deliberately HALF the verification policy's whole-gate guide of 1
   second per 1k LOC, so the other steps have the remainder. HARD CAP 60
   seconds, which is half that policy's 2-minute gate cap. Past roughly
   120k LOC the cap — not the per-LOC rate — is the binding constraint, and
   it does not move. On a project whose recorded gate budget is tighter
   than 120 seconds, that recorded figure binds before either number here.
   These limits apply to the DEFAULT SUITE only; a named profile and the
   quarantine lane are bounded by their own declared budgets instead.
10. A timeout is a HARD failure, never a shortcut to green — but the two
    limits evolve differently:
    - The per-test timeout catches one slow or hanging test. Raising it is
      almost always the wrong fix; repair the test instead (usually by
      mocking its I/O per rule 7).
    - The suite budget may SHRINK freely. It may grow only as a
      deliberate, reviewed maintenance change (see Maintenance triggers)
      that tracks more genuinely-fast tests AND satisfies rule 16, and only
      after measured runtime has crossed 80% of the current budget with
      every individual test still fast. A suite comfortably under budget
      MUST NOT relax up toward the guide or the cap: both are ceilings,
      never entitlements.
    When it can, the gate SHOULD surface these time-limit and performance
    rules on an over-budget failure, so the developer's reflex is to fix the
    tests, not to raise the budget.
11. A PERFORMANCE failure is a HARD failure, and it is treated as at least
    as serious as a functional one — often more so, because a functional
    failure is one broken behavior while a slow or hanging suite is usually
    a broken ARCHITECTURE that will keep producing bugs.
    - A suite that is slow, that hangs, or whose runtime grows
      superlinearly is a DEFECT to diagnose, never a cost to absorb and
      never a number to raise.
    - The usual root cause is a missing seam: production code that cannot
      be exercised without real I/O, real subprocesses, real sleeps, a real
      network, or a real agent. That is the signature of tests bound to
      internals instead of driving the system as a BLACK BOX through
      injectable seams. A test that must reach through to the real world to
      run is telling you the design has no seam there — add the seam.
    - Fix the coupling in the production design. Do NOT raise the timeout,
      do NOT split the suite to dodge the budget, and do NOT mark the test
      skip/xfail to make the gate green.
12. Every bug fix to behaviour in the automated lane MUST add a regression
    test that fails on the bug and passes on the fix. The test name SHOULD
    encode the regression so future readers understand the contract. A bug
    fix to behaviour routed to another lane records its regression evidence
    in THAT lane instead — it is never left unverified.
13. Every new behaviour routed to the automated lane MUST add positive
    coverage. Negative coverage is mandatory when rejection, failure,
    permission, boundary, or recovery behavior exists. New behaviour routed
    to another lane records its evidence there.
14. Shape the suite so the cheapest, most isolated layers carry the most
    cases. The count of tests SHOULD grow as their scope narrows: many fast
    tests at the lowest layer a unit of behavior supports, fewer integration
    tests that exercise real collaboration across a seam, and only a thin cap
    of end-to-end tests. The exact ratio is stack-dependent, NOT a fixed
    quota: backend and library code that decomposes into pure units leans
    heavily toward unit tests (the classic test pyramid), while UI-heavy or
    full-stack code legitimately shifts weight toward component and
    integration tests (the "testing trophy"). What is invariant across every
    stack is the shape: broad and cheap at the base, narrow and expensive at
    the top.
15. Match each layer to the job it does cheapest, and keep case enumeration
    OUT of the slow layers. Exhaustive branch, edge, boundary, and negative
    coverage (rule 13) belongs at the unit or component layer, where every
    case is reachable directly and runs fast. Integration tests SHOULD cover
    the contracts and failure modes across a seam that unit tests cannot see.
    End-to-end tests are the SCARCEST resource: reserve them for a small
    number of critical user journeys — the primary happy path of each, plus
    the few failure paths whose breakage would be catastrophic — never as the
    place to enumerate variations.
16. ADMISSION: every new test MUST name the distinct failure it would
    catch that no existing test already catches, and MUST name the nearest
    existing test found while checking. State the search performed (the
    command and the symbol). To decline a test on the grounds that the
    behaviour is already covered, name that existing test by file and test
    name — an unnamed claim of existing coverage is not a reason.
    WHEN UNCERTAIN, ADD THE TEST and flag it for the suite review: an
    uncertain duplicate is far cheaper than an uncovered behaviour. This
    rule exists to stop redundant mass, never to justify writing less.
    A parametrised sweep over a documented input domain satisfies this rule
    COLLECTIVELY — name the distinct failure the sweep catches, not one per
    case — so exhaustive boundary coverage under rule 15 is never blocked
    by this rule. Such a sweep also counts as ONE unit of suite mass under
    rule 26, however many cases the runner reports, so a compliant sweep
    can never manufacture a mass finding.
17. Tests MUST be order-independent and safe to run concurrently. No test
    may depend on another test having run, on the order the runner chose,
    or on state a sibling left behind. Shared mutable state, fixed ports,
    fixed temporary paths, shared working directories, and unguarded global
    or environment mutation are DEFECTS, not conveniences. This is not
    stylistic: parallel execution is the project's primary lever for
    keeping the suite inside its budget as it grows, and one order-coupled
    test forfeits that lever for the whole suite.
18. Tests MUST NOT depend on ambient machine or environment properties
    that the project does not pin: locale, timezone, character encoding,
    filesystem case-sensitivity or path separator, CPU count, available
    memory, word size, or shell and PATH contents. Where behaviour legitimately
    varies across these, pin the value explicitly in the test rather than
    inheriting the machine's. A test that passes only on its author's
    machine is a defect regardless of how reliably it passes there.
19. Tests MUST be structure-insensitive and behavioral. Assert what the
    unit does, not how it is built. Asserting on private internals, call
    counts and call order, log line text, or the exact wording of a
    human-readable message is forbidden UNLESS that string or sequence is
    a contract published BY THIS PROJECT — in which case cite in the test
    where it is published (a schema, an API-compatibility entry, a
    documented output contract). A third party's message wording is never
    such a contract. A test that must be edited during a refactor that
    changed no behavior is a defect in the test.
20. A test MUST be able to fail. Zero-assertion tests, tests whose
    assertions sit behind a conditional that can skip them, tests that
    assert a constant against itself, and tests that assert only that no
    exception was raised when the unit's contract is a value are all
    DEFECTS. Before admitting a test, confirm it fails when the behavior
    is broken — that is the purpose of the WRITE instruction under AI
    execution instructions below. Where that instruction's narrow
    exemption applies, demonstrate falsifiability instead by mutating the
    artifact and observing red; the exemption is from ORDERING, never from
    falsifiability.
21. A test MUST NOT assert a property a static gate already proves. Type
    conformance, lint rules, formatting, and schema validity are owned by
    the type-checking and linting policies; re-asserting them at runtime is
    pure suite mass. Test the runtime behaviour, not the declaration. This
    rule never reaches first-party code whose runtime JOB is to validate,
    parse, reject, or serialize: exercising a parser or validator is
    testing behaviour, not re-declaring a static property, and such a test
    is never removable under this rule.
22. Absolute wall-clock duration, throughput, frame rate, and memory
    footprint are NEVER default-suite assertions. They are hardware-,
    load-, and cache-dependent, so in the default suite they produce a
    test that is flaky everywhere and meaningful nowhere. Route them to a
    named profile on declared hardware, compared against a recorded
    baseline rather than a constant. This governs assertions written INSIDE
    tests. It does NOT govern the runner-enforced per-test and suite
    timeouts required by rules 8-10, which are budget gates on the suite
    rather than measurements of the code under test; a red timeout is never
    excused by citing this rule.
23. Generative and property-based tests MUST run from a PINNED seed
    committed in the repository; printing a fresh random seed each run
    satisfies neither this rule nor rule 5, because the test can then pass
    or fail without a change in behaviour. A randomized-seed exploration
    belongs in a named profile. Any counterexample it shrinks MUST be
    committed as a fixed regression test, whether or not a bug fix
    accompanies it. Assertions over non-deterministic generated output —
    including model or LLM output — do NOT belong in the default suite;
    route them to a named profile scored against a threshold.
24. Snapshot, golden-file, and approval tests are BOUNDED tools, permitted
    only where the artifact is genuinely large and structural. Each
    snapshot MUST be small enough that its diff is reviewable, MUST be
    committed so that diff appears in the change under review, and MUST NOT
    be regenerated in the same change that made its gate red. A snapshot
    nobody reads is not coverage; it is a change detector that trains
    reviewers to rubber-stamp.
25. Coverage percentage is a FLOOR, never the target and never evidence of
    test quality: a line can be executed without its consequences being
    asserted, so a high coverage number is fully compatible with a suite
    that asserts nothing. Treat a coverage figure as a floor rather than a
    ceiling to aim at. Where the stack supports it, check assertion quality
    directly — mutation analysis over the CHANGED LINES is the strongest
    available signal and is bounded enough to run per change — and MUST NOT
    raise a coverage threshold as a substitute.
26. SUITE MASS is a governed quantity, not a free variable. Record
    `suite_test_count_command` — a command that prints the current test
    count — and review the trend at the suite review. A suite whose count grows faster than the distinct
    failures it can catch is degrading even while every individual test
    stays fast and green, because the cost of a test is paid on every run,
    every refactor, and every review — not only in seconds. Use the
    assertion-quality signal from rule 25, or the distinct-failure count
    recorded at the last suite review, as the comparison; growth without a
    matching growth in covered distinct failures is resolved by retirement
    (see Test optimization and retirement), not absorbed.
27. FLAKE QUARANTINE is a named, bounded, owned lane — never a synonym for
    skipping. A non-deterministic test MAY be moved to quarantine so it
    stops blocking merges, and only under all of these conditions: it is
    recorded with an owner and an expiry date, it continues to be run and
    reported somewhere an owner sees it, and it is fixed or DELETED when
    that expiry passes. Quarantine with no deadline is deletion with extra
    ceremony and worse honesty. The expiry MUST be short — a week is the
    common default — and MUST NOT be extended; a second quarantine of the
    same test is a deletion. The quarantine MUST also be bounded in size by
    a recorded maximum; once that maximum is reached, no further test may
    be quarantined until the backlog is cleared. Quarantined tests run
    outside the DEFAULT SUITE's budget so a hanging test cannot block
    merges from quarantine — but never unbounded: they run under their own
    runner-enforced timeout, recorded alongside the expiry and maximum size
    in `quarantine_mechanism_expiry_and_max_size`, because rule 8 admits no
    suite with no enforced limit. Deleting a test at its quarantine expiry
    is the ONE removal exempt from rule 29's observed-green bar; it
    requires citing the quarantine record, its owner, and its expiry date.
28. The suite MUST run to completion from a clean clone on a supported
    developer machine, using only the project's documented setup and its
    documented secret mechanism. Any test that requires a credential a
    contributor cannot obtain that way, a hand-provisioned account, or a
    resource that will not exist next quarter does NOT belong in the
    default suite — route it per the Suite admission section. This rule
    governs the DEFAULT SUITE; a named profile is permitted precisely
    because it cannot meet this bar.
29. REMOVAL IS NEVER A ROUTE TO GREEN. A failing test MUST NOT be deleted,
    un-collected, or otherwise removed. Removing a test requires observing
    it GREEN immediately beforehand and RECORDING that run — the command
    and its actual output, dated, on the change — and the removing change
    MUST NOT be the change that made it red. When a test fails, the only
    lawful moves are to fix the production code or to fix the test, and
    "fixing the test" never means weakening an assertion so a later change
    can retire it as vacuous. The grounds for a lawful removal are
    enumerated in Test optimization and retirement; the sole exemption from
    the observed-green bar is a quarantine expiry under rule 27. This rule
    is stated here, among the mandatory requirements, because it is the one
    rule an agent under gate pressure has the strongest incentive to lose.

## Suite admission

Not every acceptance criterion belongs in the automated suite, and forcing
one in is how suites become large, slow, and untrusted. Before writing a
test, route the criterion to one of the lanes below — or, if none fits, to
a named destination recorded with an owner — and record the routing where
the change is reviewed.

1. AUTOMATED TEST — the default lane. Use it when the criterion is
   objective, decidable by the machine, expressible as a stable observable
   contract, and reproducible from a clean clone (rule 28). Most behavior
   lands here. A criterion that could land here MUST NOT be routed
   elsewhere, and inconvenience is never a routing reason: if the obstacle
   can be removed by an in-memory fake, a smaller fixture, or a seam (rules
   6-7), this lane wins.
   A criterion measured against a PUBLISHED EXTERNAL STANDARD — an
   accessibility contrast ratio, an RFC, a wire format, a schema — belongs
   in this lane even when the surrounding judgment is perceptual. Such a
   threshold is stable across redesigns, so asserting it is cheap and
   durable; only the taste question around it goes to human review.
2. HUMAN REVIEW — use it when the criterion is perceptual, aesthetic,
   ergonomic, or editorial and has no published standard behind it:
   visual balance and hierarchy, whether wording reads in the product's
   voice, whether a layout feels crowded, whether an error message is
   actually helpful. These are judgments, not assertions. Encoding one as
   a numeric constant produces the worst possible test — it fails on every
   legitimate redesign and never fails on a genuinely bad result. Manual
   exploratory verification belongs here too. Route the criterion to the
   `RALPH-REVIEW:` line of the policy that owns it; if no policy in this
   project owns it, record it on THIS policy's `RALPH-REVIEW:` line so it
   still has an owner and a dated record. Automation MAY supply supporting
   evidence for such a judgment; it MUST NOT supply the verdict.
3. NAMED PROFILE — use it when the check is genuinely repeatable and worth
   keeping, but cannot meet the default suite's constraints. The qualifying
   categories are declared ONCE, in the verification policy's Gate lanes
   section, and are not restated here so the two cannot drift; they cover
   live third-party services, paid or rate-limited APIs, device and
   platform matrices, fixed hardware for a timing measurement (rule 22),
   large datasets, long soaks, release artifacts, and output that is
   non-deterministic by nature (rule 23). Routing here is invalid unless
   the same change also adds the profile as a runnable command and records
   it in the verification policy with ALL FIVE declared fields — command,
   named owner, trigger or schedule, fails-hard-when-run, and last-run date
   with staleness horizon. An unrunnable or undeclared profile is an
   unrouted criterion, not a routed one.
4. ONE-OFF EVIDENCE — use it when the verification is not repeatable and
   is not meant to be: a credential that expires, a vendor sandbox that
   will be torn down, a one-time data migration or backfill, a manual
   capacity probe, a spike that answered a design question. State what
   makes it unrepeatable in a way that will still be true next quarter.
   Perform the check, record the dated command and its actual output as
   evidence on the change, and do NOT commit it as a test. A check that
   cannot pass six months from now on a clean clone is not a test; it is a
   receipt, and filing a receipt in the suite converts it into a scheduled
   failure that some future agent will "fix" by deleting the assertion.

Three consequences follow, and all are binding:

* Any check whose verdict can change WITHOUT a change in this repository
  is not a default-suite test. That covers a third party's uptime, a newly
  published security advisory, a package index, a certificate authority,
  and DNS. "Does the vendor's API respond right now" is a monitoring and
  alerting question; route it to the reliability lane the project uses for
  operational alerts, with a named alert and an owner. "Does our client
  handle the vendor's documented responses and failures" is the automated
  lane against a fake, with a contract test in the named-profile lane to
  detect drift in what the vendor actually returns.
* When two lanes both seem to apply, the properties of the CHECK decide,
  not the convenience of the credential. A check needing a paid or
  rate-limited API is a named profile even when the key happens to be
  obtainable locally.
* Routing to any lane other than the automated one NEVER means the
  criterion goes unverified. Each lane carries its own evidence and its
  own owner, and that evidence is DATED and RECORDED ON THE CHANGE in
  every lane — the command and its actual output where a command was run,
  the reviewer and verdict where a judgment was made. A criterion with no
  lane, no owner, and no record is unverified, and that is a blocker.

## Test optimization and retirement

Optimize a slow or over-budget suite before changing its limits. Apply these
moves while preserving the clearest test for every contract:

1. Delete TDD-scaffolding coverage once a clearer test pins the same behavior;
   never delete the sole coverage for a live contract.
2. Collapse an integration case to a unit test when an injectable seam can
   express the same observable contract more cheaply.
3. Retire an end-to-end test once a deterministic integration test covers the
   same contract; retain the smallest higher-layer test for boundaries the
   lower layer cannot observe.
4. Restore parallelism by fixing the order-coupling (rule 17) rather than
   pinning the offending tests to serial execution.

Retirement is maintenance, not loss — but it is NEVER a route to green.
A FAILING TEST MUST NOT BE RETIRED. Retirement requires the test to be
observed GREEN immediately before removal, and the retiring change MUST NOT
be the change that made it red. When a test fails, the only legal moves are
to fix the production code or to fix the test.

Subject to that bar, a test MUST be removed when any of the following is
true, and removing it needs the same evidence as adding one — name what is
no longer covered and why that is correct:

* It asserts nothing, or its assertions cannot fail (rule 20).
* It duplicates the observable contract of an existing cheaper test and
  catches no distinct failure of its own (rule 16).
* It pins internals, call sequences, or message wording that are not part
  of a contract published by this project (rule 19).
* It asserts a property a static gate already proves (rule 21).
* It pins the behavior of a third party this project does not own, rather
  than this project's handling of that behavior.
* It passes only under a particular locale, timezone, encoding, CPU count,
  or filesystem semantics the project does not pin (rule 18).
* Its fixture has expired: an embedded date, certificate, token, or vendor
  snapshot that no longer reflects reality. Regenerate it against a
  controlled clock, or retire it.
* The behaviour it covers is unreachable from any production entry point —
  delete the code, then the test.
* It has passed its quarantine expiry without a fix (rule 27).
* The behavior it covered has been deleted.

A test deleted for one of these reasons is not "lost coverage" and MUST NOT
be reinstated to raise a count or a percentage.

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

Three prerequisite facts exist and are NOT interchangeable, so record each
one distinctly: `clean_clone_setup_command` is the one-time bootstrap that
takes a fresh clone to a runnable state (rule 28);
`test_command_prerequisites` records only what `test_command` needs BEYOND
that bootstrap, and is `none` whenever the bootstrap suffices; the
verification policy's `gate_prerequisites` covers the whole gate, of which
the suite is one step.

The project's named non-default profiles are recorded once, in the
verification policy's `required_verification_profiles` fact, so there is a
single list with a single owner. Do not duplicate that list here.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* ROUTE each acceptance criterion through the Suite admission lanes before
  writing a test, and state the lane by name.
* WRITE the test before the production change and report the observed red
  result. If it unexpectedly passes, confirm existing behavior and refine the
  missing contract. The one exemption is a test over a static data file or a
  generated artifact where no production code executes: record why, and
  demonstrate falsifiability by mutating the artifact instead. Never
  manufacture a failure.
* NAME the distinct failure each new test catches, the nearest existing test
  found, and the search performed. When uncertain, add the test.
* MOCK real I/O by default, and pin every ambient dependency the test does
  not own — clock, timezone, locale, encoding, random seed.
* KEEP every test order-independent and parallel-safe.
* PREFER existing test helpers, fixtures, and utilities. Do not add a
  new testing dependency when the existing stack can express the case.
* RETIRE superseded coverage in the same change that supersedes it, only
  after observing it green, and say what was removed and why.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy in the same workflow that changes the test command,
  framework, isolation strategy, mocking approach, parallelism mechanism,
  profile set, or time budget.

An agent MUST NOT:

* Default to white-box tests that couple to private internals.
* Assert a subjective judgment — visual balance, tone, "feels right" — as
  a machine constant instead of routing it to human review.
* Commit a check that depends on an expiring credential, a temporary
  sandbox, or a hand-provisioned resource as a default-suite test.
* Add a test that catches no failure an existing test does not already
  catch, or that cannot fail at all.
* Take ANY action whose effect is that a previously-executing assertion no
  longer runs or no longer fails. This includes, and is not limited to:
  skipping, xfail, conditional skipif, deselection by name or marker,
  un-collection, renaming a file out of collection, DELETING THE TEST,
  runner-level rerun or retry configuration, reducing parallelism to mask
  order-coupling, lowering a coverage threshold, and raising, disabling, or
  removing a time limit. Retirement under the Test optimization and
  retirement section is the only lawful removal, and only from green.
* Treat quarantine as a destination. A quarantined test is fixed or
  deleted by its expiry date, and its expiry is never extended.
* Introduce wall-clock sleeps or uncontrolled external I/O.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet (for example the tool is
not installed on a new project) is recorded as a RALPH-PENDING deferral —
`RALPH-PENDING: <approved-tool> (assumed <date>); review trigger: <trigger>`
— which reaches readiness and is resolved by a later dev cycle when its
trigger fires; only a gate that truly cannot EVER exist is recorded as
inapplicable with a reason and the condition that would create it. The gate
MUST enforce the suite time limit recorded in the RALPH-FACT lines above
(via the runner's timeout, not a manual stopwatch).
Record the real gate command and confirm it EXISTS and enforces a timeout
(you MAY run it once as a bounded probe, capped at ~10s, to check that it
resolves). Do NOT fix failing tests or run the suite to green — a failing
or slow suite is the project's problem to address later. Run only the
commands you declare here. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review the suite for tests that catch no distinct failure, tests that cannot fail, criteria misrouted into automation instead of human review, order-coupled tests, expired fixtures, and quarantined tests past expiry; evidence: dated suite review recording the test count, the distinct-failure count this suite can catch, tests admitted, tests flagged as uncertain duplicates under rule 16, tests retired with the green run that authorized each removal, rule conflicts recorded since the last review, and quarantine status; owner: the named person or team recorded in the suite_review_cadence_and_owner fact

The expected successful result is a deterministic test suite that finishes
within its enforced time budget. On failure, report the failing test names
and the failure category (assertion failure, collection error, timeout,
environmental). A `timeout` category is a HARD failure: fix the slow test,
never the budget.

The suite review recorded on the `RALPH-REVIEW:` line above certifies the
judgment no runner can make: whether each test still earns its place. Rules
16, 19, 20, 21, 26, and the Suite admission section depend on it, so it runs
on the cadence recorded in `suite_review_cadence_and_owner` and a review
older than that cadence is itself a finding. It is not optional because the
automated gate is green — a suite of worthless tests passes every gate this
policy can automate.

## Exceptions

A narrower scope (e.g. no negative tests for purely declarative YAML
schemas) requires a documented rationale in this section, the scope of
the exception, and the owner of the exception. Exceptions expire at the
next policy review; an expired exception without an updated rationale
is treated as a violation.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* The test framework, test runner, or test command changes.
* A new test layer is introduced, or a named profile is added or retired.
* The test isolation strategy, mocking approach, or fake-injection pattern
  changes.
* The suite time budget, per-test timeout, timeout enforcement mechanism,
  or parallel execution mechanism changes.
* The verification policy's gate time budget changes — the suite budget is
  drawn from it, so a change there restates the ceiling here.
* The test count reported by `suite_test_count_command` grows without a
  matching growth in the distinct failures the suite can catch.
* A test is quarantined, or a quarantined test reaches its expiry.
* The recorded suite review is older than the cadence recorded in
  `suite_review_cadence_and_owner`.
* Coverage thresholds, assertion-quality checks, or other quality bars
  change.
* The supported platform matrix changes.
* A new test dependency is added or an existing one is replaced.

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
