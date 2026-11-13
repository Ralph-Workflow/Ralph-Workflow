<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: execution-kernel.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Execution Kernel

## Purpose and scope

This kernel is the generic, outcome-oriented execution layer every AI agent
must follow when working in a project that participates in Ralph Workflow's
project-policy-readiness preflight. It is always loaded: domain-specific
policies (testing, security, verification, etc.) are loaded selectively
based on the changed surfaces (B2). The kernel is the single source of
truth for rules that no domain can override; domain starters complement
the kernel, they do not repeat it (B10).

## Kernel rules

The five kernel rules are mandatory on every AI action. They are not
aspirational, not optional, not negotiable.

1. **Truthful evidence.** Every claim about behavior, status, or outcome
   must be backed by a record the agent can show: a command and its
   actual output, a test and its result, a file read and its content.
   Claims that are NOT backed by an inspectable record are not
   evidence. A claim of compliance, a recorded finding, a test result,
   or a gate outcome must be reproducible from the inputs the change
   actually executed. A claim without backing is a violation, not a stretch.
   (evidence: strong)
2. **Run every gate.** Run every `RALPH-COMMAND:` gate declared under the
   affected policy's Verification section before claiming compliance; report
   the actual outcome (exit code, error, real timing). A check that cannot run
   is a red gate; the run does not proceed past the check by default. A guard
   that times out, a test that errors on collection, a scanner that crashes
   is a HARD failure. Repairing the gate is the only lawful move;
   weakening the gate is a violation.
   (evidence: strong)
3. **No weakening.** A global silencer, a blanket ignore, a lowered
   coverage threshold, a deselected test, a raised budget, a removed time
   limit, a `disabled` audit, an inline lint or type suppression, or any
   other relaxation that hides a real defect is a violation. The only
   lawful way past a red gate is fixing the production code or fixing the
   test — never the gate. A suppression lives in tool configuration
   (`pyproject.toml` per-file settings, mypy configuration, bandit
   configuration), never inline; the underlying warning is fixed in code.
   (evidence: strong)
4. **Policy follows reality.** Update policy facts, commands, and
   requirements in the same workflow that changes the underlying tool,
   gate, exclusion, or behaviour.
   (evidence: none)
5. **Report failures honestly.** When a gate fails or a claim cannot be
   backed, the agent MUST report the failure honestly and surface the gap
   in the result body. Hiding a failure behind narration, agent summary, or
   "this is unrelated" is a violation. A red gate hidden behind narration
   is itself a failure.
   (evidence: none)

## Agent rules

The five kernel rules above are the floor and bind every action. The
rules below extend the kernel to the phases that use them. Each rule
carries an evidence tag — `strong` (a controlled study or a primary
engineering source), `moderate` (secondary or vendor data),
`anecdotal` (practitioner experience), or `none` (judgment) — and an
enforcement status resolved in the rule-to-enforcer ladder below. The
evidence tag and the enforcement status are separate properties: the
tag names the kind of source behind the rule, the enforcement status
names who decides it. The `strong` and `moderate` tags name a primary
or secondary engineering source; a rule tagged `anecdotal` or `none`
rests on judgment or practitioner experience rather than a recorded
source. A rule whose enforcer does not exist yet stays
advisory with a review trigger and is never marked `deterministic`. A
rule a deterministic gate decides carries
`deterministic` whatever its evidence tag: the absolute comment and
docstring ban in `clean-code-policy.md` is a project rule tagged `none`,
and the comment audit enforces it.

Every rule below is an agent rule or a runtime rule. No rule asks an
agent to count its own retries, to keep state across sessions, to
detect its own loops, or to police its own budget: the runtime owns
those. One runtime rule owns the repeated-failure retry: when the
same failure summary repeats, the retry formatter adds the line
"test the premise shared by the failed fixes before another patch"
(`ralph/recovery/retry_prompt.py`). The formatter line is the whole
requirement: agents never count retries, never remember earlier
attempts, and never infer that a failure repeated. (evidence: anecdotal)

1. **Check first.** Before implementing, name the runnable pass/fail
   check that decides whether the change is done, and write it down.
   (evidence: strong)
2. **Real-artifact evidence.** The evidence a completion claim carries
   lands in the `development_result` body and names the real artifact
   the change touched. (evidence: strong)
3. **Test integrity.** In the same change that touches production
   code, an agent does not modify, weaken, or delete an existing
   assertion, fixture, or expected value unless the change records
   why the old expectation was wrong. Production code does not branch
   on a test or CI environment, and no test input is special-cased to
   produce a passing result. The project rule that a test is never
   changed to match the implementation still governs (see the project
   `AGENTS.md`); this rule adds the recording requirement and the
   environment-branch ban, and does not restate that rule. When a
   test looks wrong, the agent reports it and stops instead of
   editing the expectation. (evidence: strong)
4. **Regression tests from the spec.** A bug-fix test's expected value
   is written down from the spec rather than computed by calling the
   code under test, and the test fails on the pre-fix revision. When
   the behaviour belongs to a third-party library, that library's own
   documentation is the spec: check it before relying on the behaviour.
   (evidence: moderate)
5. **Independent review.** Review belongs to the analysis phases
   (`planning_analysis`, `development_analysis`), which are already
   separate cold invocations. The reviewer judges the work against
   the plan and the spec, reports correctness gaps rather than style,
   and treats a finding as actionable only after reproducing it in a
   run; a finding that was not reproduced is reported as unverified.
   An AI verdict is never the sole evidence of correctness — the
   deterministic gate decides. Findings reuse the project's existing
   `Expected` / `Observed` / `Evidence` / `Location` shape; no second
   finding shape is introduced. As an agent-chain configuration
   recommendation (evidence: moderate, advisory): run the analysis
   phases in a different model family from the phase whose work they
   review, and re-check the recommendation when the harness records
   per-phase model families. (evidence: moderate)
6. **Handoff by artifact.** The verification command, its exit code,
   and the output that matters go into the `development_result` body,
   and every item that could not be verified is listed there as
   unverified, so the next cold agent can see it. The handoff carries
   prose and adds no new mechanically validated field: a field nobody
   validates is a promise, not a contract. (evidence: moderate)
7. **Smallest change.** Subtract before adding: prefer deleting,
   narrowing, or simplifying over introducing new machinery. A change
   that replaces an API migrates its callers and deletes the replaced
   API in the same change; a legacy path that survives its replacement
   is dead weight, not a migration. (evidence: anecdotal)
8. **Context hygiene.** Bulk reads, broad searches, and log analysis
   are delegated to sub-agents, and what comes back is the
   conclusion — the answer, the citation, the count — not the raw
   payload. Native sub-agent tools are never disabled: a missing
   orchestration facility is setup to repair, not a reason to work in
   the parent session. (evidence: strong)
9. **Plan scaling.** A trivial, single-file change goes straight to
   implementation. Multi-file or ambiguous work requires a written
   plan with acceptance criteria before the first edit, so that "done"
   is a stated criterion rather than a judgement call at the end.
   (evidence: none)
10. **Commit-level hygiene.** One logical change per commit, and the
     commit message lists the verification evidence and anything left
     unverified. Commit-level rules stop at the commit: this kernel
     states no pull-request rules. (evidence: none)
11. **Human checkpoints.** An agent asks for confirmation only for an
     irreversible or outward-facing action — publishing, deleting,
     sending, spending, granting access, or anything a human cannot
     undo. For every other blocked step the agent records the
     question and proceeds with the safest reversible assumption, so a
     missing answer does not stall the run. (evidence: none)
12. **Rule-to-enforcer ladder.** A rule that keeps being broken is
     fixed at the highest level that can hold it: architecture first,
     then types and lint, then tests, and documentation last. The
     enforcer is recorded in the guard inventory so the rule survives
     a kernel bump; a rule no command decides records its review
     trigger in the rule-to-enforcer ladder instead. A rule that has
     become impossible to break is deleted instead of being left as
     prose. (evidence: none)
13. **Non-inferable instruction surface.** The managed `AGENTS.md`
     block and the project `AGENTS.md` carry only content an agent
     cannot infer from the repository: the verify commands, the
     run-one-test command, warnings about misleading code, constraints
     that reach beyond the repository, and corrections the agent must
     repeat. An LLM-generated bulk summary of the codebase is excluded:
     it restates what a reader derives by reading the code.
     (evidence: strong)

## Rule-to-phase table

A cold agent loads the kernel plus only the slice its phase needs.
The table maps each role to the rules that bind it; a rule outside a
role's slice is not that role's obligation. This extends the
selective-loading rule: the kernel is always loaded, and the rule
slice is what a phase reads.

| Role | Phase | Rule slice | What the role hands on |
|---|---|---|---|
| Planner | `planning` | Plan scaling (9), acceptance criteria, Check first (1) | a plan whose items name the check that decides each one |
| Developer | `development` | Check first (1), Real-artifact evidence (2), Test integrity (3), Regression tests from the spec (4), Handoff by artifact (6), Smallest change (7), Context hygiene (8), Human checkpoints (11), comment hygiene (owned by `clean-code-policy.md`) | a `development_result` body carrying the command, the exit code, the output, and the unverified items |
| Analysis agents | `planning_analysis`, `development_analysis` | Independent review (5), evidence reproduction, the `Expected` / `Observed` / `Evidence` / `Location` finding shape | a choice artifact whose actionable findings were reproduced and whose unreproduced ones are marked unverified |
| Commit agent | `development_commit` | Commit-level hygiene (10) | one commit per logical change, its message listing the verification evidence and the unverified items |
| Policy remediation and policy analysis agents | `policy_remediation`, `policy_remediation_analysis` | the fill model (every `RALPH-FACT:` and `RALPH-COMMAND:` placeholder resolved against a verified project signal), Enforcement ladder (12), Non-inferable instruction surface (13) | a policy pack whose facts, commands, and guard-inventory enforcement resolve against real project signals |

## Rule-to-enforcer ladder

<!-- REPLACE-ME: resolve each rule's enforcement status here — 'deterministic: <command-id>'
matching a declared RALPH-COMMAND for a rule a command decides, or 'advisory: <reason>;
review: <trigger>' for a rule a reviewer decides. A rule marked deterministic needs a
guard-inventory row carrying the same Enforcement cell. Then delete this comment. -->

Each rule is enforced at one level; the level names where a fix for a
repeated mistake belongs, not where the rule is read.

The enforcer is the guard that decides the rule. A rule whose status
is deterministic owns a guard-inventory row, and that row's
`Enforcement` cell resolves the enforcer id this project uses. The
status column below is the choice for every rule, including the
advisory ones that own no inventory row.

| Rule | Enforcement level | Enforcer | Enforcement status |
|---|---|---|---|
| 1 Check first | tests | the focused check named for the change | advisory: no gate reads the named check; review: when a change reaches the gate without one |
| 2 Real-artifact evidence | tests | the project's verification gate | deterministic: PROJECT-FACT-UNRESOLVED |
| 3 Test integrity | tests | the project's test-policy audit | advisory: no audit detects a rewritten expectation; review: when a change edits a test expectation or adds a test-only branch |
| 4 Regression tests from the spec | tests | the project's regression test inside the test gate | advisory: the gate proves the test passes now, not that its expected value came from the spec; review: when a bug-fix change ships with an expected value copied from the implementation |
| 5 Independent review | architecture | the analysis phase, with the deterministic gate deciding the outcome | advisory: an analysis verdict is a review, not a gate; review: on every analysis phase that returns a verdict |
| 6 Handoff by artifact | docs-last | the artifact contract | advisory: the body is prose and only the frontmatter is validated; review: when the artifact contract changes |
| 7 Smallest change | architecture | the architecture review | advisory: no gate measures a diff against intent; review: when a change introduces machinery that replaces existing code |
| 8 Context hygiene | docs-last | the shared sub-agent guidance | advisory: context volume is not observable from outside the session; review: when a session's context crowds out the work unit |
| 9 Plan scaling | docs-last | the plan artifact | advisory: plan presence is judged in the planning phase, not gated; review: when a multi-file change reaches development without a written plan |
| 10 Commit-level hygiene | docs-last | the commit phase | advisory: commit contents are not gated; review: at every commit boundary |
| 11 Human checkpoints | architecture | the runtime's confirmation boundary | advisory: the runtime cannot classify every irreversible action; review: when an agent blocks on a reversible step |
| 12 Rule-to-enforcer ladder | docs-last | the guard-inventory review | advisory: the inventory review is a recurring human review; review: at the guard-inventory review cadence |
| 13 Non-inferable instruction surface | docs-last | the guard-inventory review | advisory: only line-count and path-existence checks run, and neither detects an inferable line; review: on every policy remediation pass that edits AGENTS.md content, and whenever a managed-block or AGENTS.md line-budget warning fires |

## Precedence and selective applicability

The kernel has absolute precedence over domain policies on the
kernel rules and the agent rules above. When a domain starter, a
project fact, a remediation prompt, or a developer comment contradicts
a kernel rule, the kernel wins. A domain starter may ADD a rule,
never REMOVE or WEAKEN one. Shipped skills hold technique; where a
skill's guidance conflicts with this kernel, the kernel (policy) wins.

Agents load the kernel plus only the domain policies triggered by the
changed surfaces. The kernel is always loaded; conditional domain
policies load when their declared triggers fire (B2). The default for
a conditional starter is "do not load it" — the starter's own
"Applicability" section names the trigger; the AGENTS.md managed block
restates the rule; the remediation prompt enforces it. Loading every
policy on every change is the symptom the kernel explicitly rules out.

The rule-to-phase table extends that rule one level down.
Load order: kernel → role slice → triggered domain file → stop. A
cold agent reads the kernel, then only the rule slice its phase binds,
named in that table, then the domain file(s) whose trigger the change
actually fires. A rule outside a role's slice is not that role's
obligation, and an agent that never runs that phase never loads it.

Domain starters reduce their `## AI execution instructions` to
domain-specific steps plus a one-line pointer to this kernel. They
do not rephrase the five rules. The "## Guard inventory" section on
each starter records the per-domain guards, each with a seven-cell
row: Guard, Owner, Protected outcome, Detected condition, Claim scope,
Enforcement, Retirement trigger (B7, B9). The inventory is the only
way a domain rule survives a kernel bump; a rule in body prose without
an inventory row is not enforceable.

## Claim-scope rule

Every guard's Protected outcome MUST be a claim about what the
detector actually observes, never about the world at large. A
broad guarantee ("dependency direction enforced", "all inputs
validated", "security guaranteed") is rejected by the deterministic
substring predicate unless the same phrase ALSO appears in the
Detected condition. The rule's purpose is to stop manufactured
certainty: a check that reports "missing source-import-lint finding"
does not, by itself, prove "dependency direction enforced".

The substring predicate is the deterministic floor; a human-review
rubric item covers the residual semantic gap. The paired replay
(step D) scores outcomes for both: a control that survives the
substring check but whose protected_outcome overstates the detector
loses the human-review score.

The same rule applies to portfolio controls: every control's
`protected_outcome` is checked against the new required `detects`
field (portfolio schema v3) using the same predicate. A portfolio
control with a broad outcome and a narrow `detects` is rejected
on portfolio parse.

## Guard inventory

<!-- REPLACE-ME: resolve each row's Enforcement column to 'deterministic: <command-id>'
matching a declared RALPH-COMMAND or 'advisory: <reason>; review: <trigger>'.
Then delete this comment. -->

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| Truthful evidence | Verification owner | every claim is backed by a record the agent can show | RALPH-COMMAND: present and exit-code recorded; no narration-only claim in the result body | RALPH-COMMAND: present and exit-code recorded | deterministic: PROJECT-FACT-UNRESOLVED | when a claim-presentation proof replaces the recorded evidence with a published artifact |
| Run every gate | Gate-script owner | a check that cannot run blocks the run | gate script returns non-zero on collection error, missing tool, or timeout | gate script returns non-zero on collection error, missing tool, or timeout | deterministic: PROJECT-FACT-UNRESOLVED | when the same defect cannot recur (e.g. the tool is no longer wired) |
| No weakening | Verification owner | no bypass mechanism lands in the tree; a suppression lives in tool configuration (`pyproject.toml` per-file settings, mypy configuration, bandit configuration), never inline, and the underlying warning is fixed in code | project linters, typecheckers, test-policy guards, and verification audits return zero bypass-mechanism findings | project linters, typecheckers, test-policy guards, and verification audits return zero bypass-mechanism findings | deterministic: PROJECT-FACT-UNRESOLVED | when the same defect cannot recur (e.g. the rule is removed from policy) |
| Policy follows reality | Policy owner | the policy facts and commands match the tool they describe | the policy file names the command the repository actually runs | the command named in the policy file | deterministic: PROJECT-FACT-UNRESOLVED | when a gate checks the policy files against the repository |
| Report failures honestly | Verification owner | a red gate is reported as a red gate | the result body reports the gate outcome instead of hiding it behind narration | the gate outcome reported in the result body | deterministic: PROJECT-FACT-UNRESOLVED | when the result body gains a validated field |
| Real-artifact evidence | Verification owner | the claim cites the command, its exit code, and the output that matters | the result body records the command and its exit code | the exit code and output recorded for the claim | deterministic: PROJECT-FACT-UNRESOLVED | when the recorded evidence is produced by the gate without an agent step |
| Independent review model-family diversification | Analysis owner | the analysis phases run in a different model family from the phase whose work they review | the agent-chain configuration records the model family of the analysis phase and it differs from the authoring phase's model family | the model-family assignment recorded in the agent-chain configuration | advisory: no gate reads the agent chain's model family assignment, so a different-model-family review stays a configuration recommendation; review: when the harness records the per-phase model family in the run ledger | when a gate verifies the analysis phase runs in a different model family from the phase under review |
| Non-inferable instruction surface | Policy owner | the instruction surfaces carry no line a reader can infer from the code | the recorded instruction-surface review names the lines of the managed AGENTS.md block and the project AGENTS.md a reader can infer | the instruction-surface lines named in the recorded review | advisory: only line-count and path-existence checks run, and neither detects an inferable line; review: on every policy remediation pass that edits AGENTS.md content, and whenever a managed-block or AGENTS.md line-budget warning fires | when a gate decides the content of the instruction surfaces |
| Premise-check retry line | Recovery owner | the repeated-failure retry prompt names the premise shared by the failed fixes | the retry formatter adds the premise-check line when the current and prior failure summaries match (`ralph/recovery/retry_prompt.py`) | the premise-check line the retry formatter adds on a repeated failure | advisory: only the formatter requirement on the premise-check line exists and no gate reads the retry prompt a session receives; review: when the runtime records the retry prompts delivered to a session | when a gate checks the repeated-failure retry prompt for the premise-check line |

The agent rules in `## Agent rules` are enforced through the
rule-to-enforcer ladder above. A rule whose ladder status is
deterministic owns a row here, and its Enforcement cell names the
enforcer id this project resolves. A rule whose ladder status is
advisory carries its reason and its review trigger in the ladder; it
owns a row here when the policy names who decides it.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below. `none` IS a legal resolved value when the project genuinely has no
such mechanism. If a fact cannot be resolved yet, defer it with the
RALPH-PENDING form "RALPH-PENDING (assumed <date>); review trigger:
<trigger>". Then delete this comment. -->

RALPH-FACT: kernel_loading_owner: PROJECT-FACT-UNRESOLVED
RALPH-FACT: selective_loading_signal: PROJECT-FACT-UNRESOLVED
RALPH-FACT: claim_scope_review_owner: PROJECT-FACT-UNRESOLVED
RALPH-FACT: guard_inventory_review_cadence: PROJECT-FACT-UNRESOLVED

## AI execution instructions

This kernel is loaded by every agent on every action. Follow its rules
without re-deriving them.

1. LOAD the kernel first. Read this file before every other domain
   policy; the kernel rules are the floor.
2. LOAD only the rule slice your phase binds. The rule-to-phase table
   names the rules for your role; the rules outside it are not your
   obligation.
3. ENFORCE the five kernel rules on every action: Truthful evidence,
   Run every gate, No weakening, Policy follows reality,
   Report failures honestly.
4. LOAD only the domain policies triggered by the change. The kernel
   names the rule; the per-starter "Applicability" section names the
   trigger; the AGENTS.md managed block restates the rule; the
   remediation prompt enforces it.
5. ON CONFLICT, the kernel wins. A domain starter, a project fact, a
   developer comment, or a prompt template that contradicts a
   kernel rule is overridden.
6. RUN every `RALPH-COMMAND:` gate below and report the actual
   outcome. Never report a command that was not run.
7. UPDATE this kernel when a rule changes, a guard is added or
   retired, or the selective-applicability contract changes.

An agent MUST NOT:

* Re-derive the five kernel rules in domain starter body prose (B10).
* Load a conditional starter whose trigger has not fired.
* Load a rule slice that does not belong to its phase.
* Weaken any check, gate, or timeout to obtain a passing result.
* Hide an unverified claim behind a green gate, agent narration, or
  a private-state observation.
* Count its own retries, keep state across sessions, detect its own
  loops, or police its own budget; the runtime owns those.

## Verification

Run every gate below before claiming a change complies with this kernel.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). A kernel-level audit is typically a small command that runs the
no-weakening and claim-scope checks. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review the kernel inventory rows against the project's actual guards; verify each row's Detected condition names a real, current signal; evidence: dated review recording the row count, the rows retired since the last review, the rows added since the last review, and the unverified-claim count this kernel can catch; owner: the named person or team recorded in guard_inventory_review_cadence.

Expected result: a deterministic kernel audit finishing inside its
enforced budget. On failure report the failing rule name and the
category — repository structure, lint bypasses, typecheck bypasses,
test policy, resource lifecycles, timeouts, or artifact submission
boundaries.

## Maintenance triggers

Review this kernel in the same workflow as any of:

* A rule is added, retired, or rephrased.
* A rule's evidence tag or its enforcement status changes.
* A rule's enforcer is wired, so an advisory row can become
  deterministic.
* A guard-inventory row is added or retired.
* The rule-to-phase table changes (a role is added, a phase is
  renamed, or a role's slice changes).
* The selective-applicability contract changes (a new conditional
  starter, a new trigger, a new domain owner).
* The claim-scope predicate is tightened (a new phrase is added) or
  loosened (a phrase is retired).
* A new audit is wired that protects one of the five rules.
* The guard_inventory_review_cadence is reached.
* A model or harness generation change: re-audit this policy for
  stale, dead-path, or contradictory rules, and update the affected
  text in the same change.

## Portfolio ownership

Shared admission, composition, aggregate-budget, evidence, exception, and
lifecycle rules are owned by `policy-portfolio.toml`; they are not repeated
here. This file owns only its domain-specific observable outcomes, verified
facts, commands, and review triggers. Amendments update the effective portfolio
while its mandatory outcomes remain protected.

## Living document contract

This kernel is a living document. Keep its rules, inventory, facts,
commands, and review triggers aligned with verified project reality.
Shared amendment admission and retention are governed once by
`policy-portfolio.toml`; mandatory outcomes remain protected and MUST NOT
be weakened to obtain a passing result.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: execution-kernel.md -->`
* Schema version: `<!-- ralph-policy-schema: v6 -->`

## Research basis

* publisher: Anthropic
  title: "Best practices for Claude Code"
  http: https://code.claude.com/docs/en/best-practices
  review date: 2026-11-09
  supports: Check first (1), Real-artifact evidence (2) and Context
  hygiene (8) — the guidance hands the agent a runnable pass/fail
  check, asks for the evidence behind a completion claim, and delegates
  bulk reads to sub-agents that return conclusions rather than raw
  payloads. A primary engineering source, so the rules it supports stay
  `strong`.

* publisher: Gloaguen et al. (arXiv preprint)
  title: "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?"
  http: https://arxiv.org/pdf/2602.11988
  review date: 2026-11-09
  supports: Independent review (5), Handoff by artifact (6) and
  Non-inferable instruction surface (13) — a written agent instruction
  helps the next reader only when it states what was actually checked
  and carries content the reader cannot infer, so the review is judged
  against the spec, the handoff carries the record behind the claim, and
  an instruction surface states only non-inferable requirements. The
  review and handoff rules rest on its directional findings, so they
  stay `moderate`; the repository-context effect it measured is a
  controlled study, so the instruction-surface rule stays `strong`.

* publisher: Zhong et al. (arXiv preprint)
  title: "ImpossibleBench: Measuring LLMs' Propensity of Exploiting Test Cases"
  http: https://arxiv.org/abs/2510.20270
  review date: 2026-11-09
  supports: Test integrity (3) — the benchmark measures how often a
  model exploits a test case to pass, which is the behaviour the rule
  forbids beside the production code the test covers. Only the abstract
  was read.

* publisher: Spracklen et al. (arXiv preprint)
  title: "We Have a Package for You! A Comprehensive Analysis of Package Hallucinations by Code Generating LLMs"
  http: https://arxiv.org/html/2406.10279v3
  review date: 2026-11-09
  supports: Real-artifact evidence (2) — a package name that does not
  exist is a claim with no record behind it, so the evidence for a
  completion claim has to come from the artifact the change actually
  touched. Only the abstract was read.

* publisher: Google Engineering Practices
  title: "Code Review at Google"
  http: https://google.github.io/eng-practices/review/
  review date: 2026-11-09
  supports: Real-artifact evidence (2) and Test integrity (3) — a
  reviewer reads the change against the plan, and an expectation edited
  beside the code it covers is a finding rather than a detail.

* publisher: Kent Beck
  title: "Test-Driven Development: By Example"
  http: https://www.pearson.com/en-us/subject-catalog/p/test-driven-development-by-example/P200000009421/9780321146533
  review date: 2026-11-09
  supports: Check first (1) and Regression tests from the spec (4) —
  the check that decides the change exists before the change does, and
  the expected value comes from the spec rather than from a run of the
  code under test.

* publisher: Martin Fowler
  title: "TestDesiderata"
  http: https://testdesiderata.com/
  review date: 2026-11-09
  supports: Regression tests from the spec (4) — a test earns its keep
  from the behaviour it pins down, so the expected value is written
  down from the spec rather than captured from a run. A secondary
  source, so the rule stays `moderate`.

* publisher: Cursor (pstack plugins)
  title: "pstack"
  http: https://github.com/cursor/plugins/tree/main/pstack
  review date: 2026-11-09
  supports: Smallest change (7) and
  Rule-to-enforcer ladder (12) — the practitioner notes propose
  subtracting before adding, and a rule-to-enforcer table that fixes a
  repeated mistake at the highest level that can hold it. Practitioner
  experience is not a measurement, so the rules it supports stay
  advisory.

* publisher: Ralph Workflow project
  title: "Project policy: outcome-oriented execution kernel"
  http: https://github.com/Ralph-Workflow/Ralph-Workflow
  review date: 2026-11-09
  supports: Plan scaling (9), Commit-level hygiene (10) and Human
  checkpoints (11) — this project has a plan phase, a commit phase,
  and human checkpoints for irreversible actions; the thresholds that
  separate a trivial change from a planned one, and an irreversible
  action from a reversible one, are project judgment rather than a
  measurement, so these rules stay `none` and advisory.


