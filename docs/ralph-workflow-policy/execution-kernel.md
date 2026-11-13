<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: execution-kernel.md -->

# Execution Kernel

## Purpose and scope

This file is the shared core that every domain policy file in
`docs/ralph-workflow-policy/` and every starter points to. The kernel
carries the execution rules; each domain file keeps its own
`RALPH-FACT:` / `RALPH-COMMAND:` declarations and adds only domain
outcomes, facts, triggers, and exceptions. No domain file repeats the
kernel's execution rules.

The kernel also carries the agent rules that bind the pipeline's
phases, the rule-to-phase table that hands a cold agent only its own
slice, and the rule-to-enforcer ladder that records where each rule is
enforced. The rules are the floor for every agent working in this
repository; the domain files are the ceiling.

## Ralph markers

Every policy file starts with these two markers as the literal first
non-blank lines:

```
<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: <filename.md> -->
```

The current schema marker is `<!-- ralph-policy-schema: v6 -->`. Both
markers MUST appear verbatim before any heading or content.

## Precedence and selective applicability

* The kernel wins when a domain file's wording conflicts with it.
* A domain file MAY add a stricter rule than the kernel (see
  Local-tightening review below) but MUST NOT relax a kernel rule.
* When a domain file and the kernel both cover the same situation,
  the kernel rule is the floor; the domain rule is the ceiling.
* Shipped skills hold technique; where a skill's guidance conflicts with
  this kernel, the kernel (policy) wins.

## Loading direction

* Agents MUST NOT load every policy file by default.
* Load order: kernel → role slice → triggered domain file → stop.
  A cold agent reads the kernel, then only the rule slice its phase
  binds, named in the rule-to-phase table below, then the domain
  file(s) whose trigger the change actually fires.
* The verification entry point (`make -C ralph-workflow verify`) is the
  authoritative loader, not which files an agent has read.
* A change that does not fire a domain file's trigger does not require
  that file to be loaded.

## Kernel rules

1. **Truthful evidence.** Every claim about behavior, status, or outcome
   must be backed by a record the agent can show: a command and its
   actual output, a test and its result, a file read and its content.
   Claims that are NOT backed by an inspectable record are not
   evidence. A claim of compliance, a recorded finding, a test result,
   or a gate outcome must be reproducible from the inputs the change
   actually executed. A claim without backing is a violation, not a
   stretch. (evidence: strong)
2. **Run every gate.** Run every `RALPH-COMMAND:` gate declared under the
   affected policy's Verification section before claiming compliance;
   report the actual outcome (exit code, error, real timing). A check
   that cannot run is a red gate; the run does not proceed past the
   check by default. A guard that times out, a test that errors on
   collection, a scanner that crashes is a HARD failure. Repairing the
   gate is the only lawful move; weakening the gate is a violation.
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
   gate, exclusion, or behaviour. (evidence: none)
5. **Report failures honestly.** When a gate fails or a claim cannot be
   backed, the agent MUST report the failure honestly and surface the gap
   in the result body. Hiding a failure behind narration, agent summary, or
   "this is unrelated" is a violation. A red gate hidden behind narration
   is itself a failure. (evidence: none)

## Agent rules

The shared execution rules above are the floor and bind every action.
The rules below extend the kernel to the phases that use them. Each
rule carries an evidence tag — `strong` (a controlled study or a
primary engineering source), `moderate` (secondary or vendor data),
`anecdotal` (practitioner experience), or `none` (judgment) — and an
enforcement status resolved in the rule-to-enforcer ladder below. The
evidence tag and the enforcement status are separate properties: the
tag names the kind of source behind the rule, the enforcement status
names who decides it. The `strong` and `moderate` tags name a primary
or secondary engineering source; a rule tagged `anecdotal` or `none`
rests on judgment or practitioner experience rather than a recorded
source. A rule whose
enforcer does not exist yet stays advisory with a review trigger and is
never marked `deterministic`. Only a rule tagged `strong` or `moderate`
may carry a `deterministic` enforcement status; a rule tagged
`anecdotal` or `none` ships `advisory` with a review trigger. The
single exception is the absolute comment and docstring ban in
`clean-code-policy.md`, a project rule tagged `none` that ships
`deterministic` because the comment audit (`ralph.testing.audit_comments`)
enforces it.

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
   assertion, fixture, or expected value unless the change records why
   the old expectation was wrong. Production code does not branch on a
   test or CI environment, and no test input is special-cased to
   produce a passing result. The repository's own rule that a test is
   never changed to match the implementation still governs (see the
   repository `AGENTS.md`); this rule adds the recording requirement
   and the environment-branch ban, and does not restate that rule. When
   a test looks wrong, the agent reports it and stops instead of
   editing the expectation. (evidence: strong)
4. **Regression tests from the spec.** A bug-fix test's expected value
   is written down from the spec rather than computed by calling the
   code under test, and the test fails on the pre-fix revision. When
   the behaviour belongs to a third-party library, that library's own
   documentation is the spec: check it before relying on the behaviour.
   (evidence: moderate)
5. **Independent review.** Review belongs to the analysis phases
   (`planning_analysis`, `development_analysis`), which are already
   separate cold invocations. The reviewer judges the work against the
   plan and the spec, reports correctness gaps rather than style, and
   treats a finding as actionable only after reproducing it in a run; a
   finding that was not reproduced is reported as unverified. An AI
   verdict is never the sole evidence of correctness — the deterministic
   gate decides. Findings reuse the existing `Expected` / `Observed` /
   `Evidence` / `Location` shape; no second finding shape is
   introduced. As an agent-chain configuration recommendation
   (evidence: moderate, advisory): run the analysis phases in a
   different model family from the phase whose work they review, and
   re-check the recommendation when the harness records per-phase model
   families. (evidence: moderate)
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
   are delegated to sub-agents, and what comes back is the conclusion
   — the answer, the citation, the count — not the raw payload. Native
   sub-agent tools are never disabled: a missing orchestration facility
   is setup to repair, not a reason to work in the parent session.
   (evidence: strong)
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
     undo. For every other blocked step the agent records the question
     and proceeds with the safest reversible assumption, so a missing
     answer does not stall the run. (evidence: none)
12. **Rule-to-enforcer ladder.** A rule that keeps being broken is
     fixed at the highest level that can hold it: architecture first,
     then types and lint, then tests, and documentation last. The
     enforcer is recorded in the guard inventory so the rule survives a
     kernel bump; a rule no command decides records its review trigger
     in the rule-to-enforcer ladder instead. A rule that has become
     impossible to break is deleted instead of being left as prose.
     (evidence: none)
13. **Non-inferable instruction surface.** The managed `AGENTS.md`
     block and the project `AGENTS.md` carry only content an agent
     cannot infer from the repository: the verify commands, the
     run-one-test command, warnings about misleading code, constraints
     that reach beyond the repository, and corrections the agent must
     repeat. An LLM-generated bulk summary of the codebase is excluded:
     it restates what a reader derives by reading the code.
     (evidence: strong)

## Rule-to-phase table

A cold agent loads the kernel plus only the slice its phase needs. The
table maps each role to the rules that bind it; a rule outside a role's
slice is not that role's obligation.

| Role | Phase | Rule slice | What the role hands on |
|---|---|---|---|
| Planner | `planning` | Plan scaling (9), acceptance criteria, Check first (1) | a plan whose items name the check that decides each one |
| Developer | `development` | Check first (1), Real-artifact evidence (2), Test integrity (3), Regression tests from the spec (4), Handoff by artifact (6), Smallest change (7), Context hygiene (8), Human checkpoints (11), comment hygiene (owned by `clean-code-policy.md`) | a `development_result` body carrying the command, the exit code, the output, and the unverified items |
| Analysis agents | `planning_analysis`, `development_analysis` | Independent review (5), evidence reproduction, the `Expected` / `Observed` / `Evidence` / `Location` finding shape | a choice artifact whose actionable findings were reproduced and whose unreproduced ones are marked unverified |
| Commit agent | `development_commit` | Commit-level hygiene (10) | one commit per logical change, its message listing the verification evidence and the unverified items |
| Policy remediation and policy analysis agents | `policy_remediation`, `policy_remediation_analysis` | the fill model (every `RALPH-FACT:` and `RALPH-COMMAND:` placeholder resolved against a verified project signal), Enforcement ladder (12), Non-inferable instruction surface (13) | a policy pack whose facts, commands, and guard-inventory enforcement resolve against real project signals |

## Rule-to-enforcer ladder

Each rule is enforced at one level; the level names where a fix for a
repeated mistake belongs, not where the rule is read.

The enforcer is the guard that decides the rule. The guard inventory
below records the same choice in its `Enforcement` column, so the
inventory resolves one `Enforcement` id per rule; the status column
below repeats the inventory cell verbatim.

| Rule | Enforcement level | Enforcer | Enforcement status |
|---|---|---|---|
| 1 Check first | tests | the focused check named for the change | advisory: no gate reads the named check; review: when a change reaches the gate without one |
| 2 Real-artifact evidence | tests | the authoritative verification gate | advisory: the `development_result` body is free-form and only its `status` vocabulary is validated (`ralph/mcp/artifacts/development_result.py`), so the cited command, exit code, and output are not machine-checked; review: re-check when the body schema gains required evidence fields |
| 3 Test integrity | tests | the test-integrity audit (audit_test_integrity) | deterministic: test integrity audit (audit_test_integrity) |
| 4 Regression tests from the spec | tests | the regression test inside the test gate | advisory: the gate proves the test passes now, not that its expected value came from the spec; review: when a bug-fix change ships with an expected value copied from the implementation |
| 5 Independent review | architecture | the analysis phase, with the deterministic gate deciding the outcome | advisory: an analysis verdict is a review, not a gate; review: on every analysis phase that returns a verdict |
| 6 Handoff by artifact | docs-last | the artifact contract | advisory: the body is prose and only the frontmatter is validated; review: when the artifact contract changes |
| 7 Smallest change | architecture | the architecture review | advisory: no gate measures a diff against intent; review: when a change introduces machinery that replaces existing code |
| 8 Context hygiene | docs-last | the shared sub-agent guidance | advisory: context volume is not observable from outside the session; review: when a session's context crowds out the work unit |
| 9 Plan scaling | docs-last | the plan artifact | advisory: plan presence is judged in the planning phase, not gated; review: when a multi-file change reaches development without a written plan |
| 10 Commit-level hygiene | docs-last | the commit phase | advisory: commit contents are not gated; review: at every commit boundary |
| 11 Human checkpoints | architecture | the runtime's confirmation boundary | advisory: the runtime cannot classify every irreversible action; review: when an agent blocks on a reversible step |
| 12 Rule-to-enforcer ladder | docs-last | the guard-inventory review | advisory: the inventory review is a recurring human review; review: at the guard-inventory review cadence |
| 13 Non-inferable instruction surface | docs-last | the guard-inventory review | advisory: only line-count and path-existence checks run, and neither detects an inferable line; review: on every policy remediation pass that edits AGENTS.md content, and whenever a managed-block or AGENTS.md line-budget warning fires |

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
rubric item covers the residual semantic gap.

The same rule applies to portfolio controls: every control's
`protected_outcome` is checked against the required `detects`
field using the same predicate.

## Guard inventory

The `Enforcement` column names the guard that decides each rule:
`deterministic: <command-id>` when a declared command decides it, and
`advisory: reason; review: trigger` while a reviewer decides it.
The `Enforcement` ids resolve by exact string match against the
union of the `RALPH-COMMAND:` values declared in this pack and, in
this repository, the step labels the verification gate declares. The
agent rules in `## Agent rules` are enforced through the
rule-to-enforcer ladder above: a rule whose ladder status is
deterministic owns a row here, and its Enforcement cell names the
enforcer id; a rule whose ladder status is advisory carries its
reason and its review trigger here, and owns a row because this pack
names who decides it.

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| Truthful evidence | Verification owner | the claim is reproducible from the inputs the change executed | the gate command and its exit code are recorded with the claim | the exit code and output recorded for the claim | deterministic: make -C ralph-workflow verify | when a claim-presentation proof replaces the recorded evidence with a published artifact |
| Run every gate | Gate-script owner | a gate that cannot run blocks the run | the gate script returns non-zero on a collection error, a missing tool, or a timeout | the non-zero exit code of the failing gate | deterministic: make -C ralph-workflow verify | when the same defect cannot recur (for example the tool is no longer wired) |
| No weakening | Verification owner | no bypass mechanism lands in the tree | the lint-bypass and typecheck-bypass audits return zero findings | the bypass findings the audits report | deterministic: make -C ralph-workflow verify | when the same defect cannot recur (for example the rule leaves the policy) |
| Policy follows reality | Policy owner | the policy facts and commands match the tool they describe | the policy file names the command the repository actually runs | the command named in the policy file | advisory: no gate compares a policy file with the code it describes; review: when a gate, tool, or documented behaviour changes | when a gate checks the policy files against the repository |
| Report failures honestly | Verification owner | a red gate is reported as a red gate | the result body reports the gate outcome instead of hiding it behind narration | the gate outcome reported in the result body | advisory: the result body is prose and no gate parses it; review: when an analysis phase finds a claim without backing | when the result body gains a validated field |
| Check first | Planning owner | the change names a runnable check before the implementation starts | the plan or the result body names the command that decides the change | the named check recorded in the plan or the result body | advisory: no gate reads the named check; review: when a change reaches the gate without one | when every change carries a check that a gate runs |
| Real-artifact evidence | Verification owner | the claim cites the command, its exit code, and the output that matters | the result body records the command and its exit code | the exit code and output recorded for the claim | advisory: the `development_result` body is free-form and only its `status` vocabulary is validated (`ralph/mcp/artifacts/development_result.py`), so the cited command, exit code, and output are not machine-checked; review: re-check when the body schema gains required evidence fields | when the recorded evidence is produced by the gate without an agent step |
| Test integrity | Test owner | no existing assertion, fixture, or expected value is removed or weakened beside the production code it covers, and shipped code does not read the test runner's environment | the change diff removes or weakens a test expectation beside a production edit without a recorded reason, or shipped code reads the test runner's environment | the weakened expectation or the test-runner read the test-integrity audit reports | deterministic: test integrity audit (audit_test_integrity) | when the test-integrity gate is retired |
| Regression tests from the spec | Test owner | the bug-fix test states the spec's expected value instead of the implementation's output | the test's expected value matches the value the code under test returns | the expected value written in the bug-fix test | advisory: the gate proves the test passes now, not that its expected value came from the spec; review: when a bug-fix change ships with an expected value copied from the implementation | when the spec-derived expected value is recorded and checked mechanically |
| Independent review | Analysis owner | the review reproduces each finding it treats as actionable | the choice body cites a reproduced command or a re-read location | the reproduced finding cited in the choice body | advisory: an analysis verdict is a review, not a gate; review: on every analysis phase that returns a verdict | when the analysis phase's findings are checked mechanically |
| Independent review model-family diversification | Analysis owner | the analysis phases run in a different model family from the phase whose work they review | the agent-chain configuration records the model family of the analysis phase and it differs from the authoring phase's model family | the model-family assignment recorded in the agent-chain configuration | advisory: no gate reads the agent chain's model family assignment, so a different-model-family review stays a configuration recommendation; review: when the harness records the per-phase model family in the run ledger | when a gate verifies the analysis phase runs in a different model family from the phase under review |
| Handoff by artifact | Artifact owner | the result body carries the command, the exit code, the output, and the unverified items | the result body contains a verification command and an unverified list | the verification command and the unverified list in the result body | advisory: the body is prose and only the frontmatter is validated; review: when the artifact contract changes | when the artifact body gains a validated field |
| Smallest change | Architecture owner | the change deletes or narrows before it adds, and the replaced API leaves with its callers | the change diff still carries the replaced API after its callers moved | the replaced API still present in the change diff | advisory: no gate measures a diff against intent; review: when a change introduces machinery that replaces existing code | when an audit detects a replaced API that survives its callers |
| Context hygiene | Session owner | the session keeps conclusions instead of raw payloads | the session context carries a bulk payload instead of a conclusion | the conclusion returned by the delegated read | advisory: context volume is not observable from outside the session; review: when a session's context crowds out the work unit | when the runtime measures session context volume |
| Plan scaling | Planning owner | multi-file or ambiguous work starts from a written plan with acceptance criteria | the plan artifact names the acceptance criteria for the work item | the acceptance criteria recorded in the plan artifact | advisory: plan presence is judged in the planning phase, not gated; review: when a multi-file change reaches development without a written plan | when the runtime rejects a development phase without a plan |
| Commit-level hygiene | Commit owner | the commit holds one logical change and names its verification evidence | the commit message lists the verification command and the unverified items | the verification evidence named in the commit message | advisory: commit contents are not gated; review: at every commit boundary | when a commit gate checks the message content |
| Human checkpoints | Session owner | a confirmation is asked only for an irreversible or outward-facing action | the session records the blocked question and the reversible assumption it proceeded with | the recorded question and the reversible assumption in the session | advisory: the runtime cannot classify every irreversible action; review: when an agent blocks on a reversible step | when the runtime classifies irreversible actions mechanically |
| Rule-to-enforcer ladder | Policy owner | each rule names an enforcement level and an enforcer recorded in this inventory | this inventory carries a row for every rule the kernel states | the rule rows carried in this inventory | advisory: the inventory review is a recurring human review; review: at the guard-inventory review cadence | when the inventory review becomes a scheduled gate |
| Non-inferable instruction surface | Policy owner | the instruction surfaces carry no line a reader can infer from the code | the recorded instruction-surface review names the lines of the managed AGENTS.md block and the project AGENTS.md a reader can infer | the instruction-surface lines named in the recorded review | advisory: only line-count and path-existence checks run, and neither detects an inferable line; review: on every policy remediation pass that edits AGENTS.md content, and whenever a managed-block or AGENTS.md line-budget warning fires | when a gate decides the content of the instruction surfaces |
| Premise-check retry line | Recovery owner | the repeated-failure retry prompt names the premise shared by the failed fixes | the retry formatter adds the premise-check line when the current and prior failure summaries match (`ralph/recovery/retry_prompt.py`) | the premise-check line the retry formatter adds on a repeated failure | advisory: only the formatter requirement on the premise-check line exists and no gate reads the retry prompt a session receives; review: when the runtime records the retry prompts delivered to a session | when a gate checks the repeated-failure retry prompt for the premise-check line |


## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: kernel_loading_owner: verification owner
RALPH-FACT: selective_loading_signal: docs/ralph-workflow-policy/
RALPH-FACT: claim_scope_review_owner: project maintainers
RALPH-FACT: guard_inventory_review_cadence: quarterly or when policy schema changes

## AI execution instructions

This kernel is loaded by every agent on every action. Follow its rules
without re-deriving them.

1. LOAD the kernel first. Read this file before every other domain
   policy; the kernel rules are the floor.
2. LOAD only the rule slice your phase binds, named in the
   rule-to-phase table.
3. ENFORCE the five kernel rules on every action: Truthful evidence,
   Run every gate, No weakening, Policy follows reality,
   Report failures honestly.
4. LOAD only the domain policies triggered by the change.
5. ON CONFLICT, the kernel wins.
6. RUN every `RALPH-COMMAND:` gate below and report the actual
   outcome. Never report a command that was not run.
7. UPDATE this kernel when a rule changes, a guard is added or
   retired, or the selective-applicability contract changes.

## Verification

Run the authoritative gate before claiming a change complies with this
kernel. The gate list and the per-step timeouts are owned by
`verification-policy.md`; this file names the entry point only.

RALPH-COMMAND: make -C ralph-workflow verify

Expected result: exit 0 from the authoritative gate, with the command,
its exit code, and the output that matters reported against the real
artifact the change touched.

## Local-tightening review

A domain file may add a stricter rule than the kernel. Every local
tightening MUST record inline:

* **Owner** — who is accountable for the tightening.
* **Protected outcome** — what specific risk or regression the tightening
  prevents.
* **Retirement/review trigger** — the condition or date that re-opens
  the tightening for review.

An ownerless tightening, a tightening with no protected outcome, or a
tightening with no trigger is a retirement candidate.


## Maintenance triggers

Review this kernel in the same workflow as any of:

* A rule is added, retired, or rephrased.
* A rule's evidence tag or its enforcement status changes.
* A rule's enforcer is wired, so an advisory row can become
  deterministic.
* A guard-inventory row is added or retired.
* The rule-to-phase table changes (a role is added, a phase is
  renamed, or a role's slice changes).
* The selective-applicability contract changes.
* The claim-scope predicate is tightened or loosened.
* A new audit is wired that protects one of the five rules.
* The guard_inventory_review_cadence is reached.
* A model or harness generation change: re-audit this policy for
  stale, dead-path, or contradictory rules, and update the affected
  text in the same change.

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
  checkpoints (11) — this project has a planning phase, a commit phase,
  and human checkpoints for irreversible actions; the thresholds that
  separate a trivial change from a planned one, and an irreversible
  action from a reversible one, are project judgment rather than a
  measurement, so these rules stay `none` and advisory.

## What the kernel does NOT do

* Does not list domain-specific rules.
* Does not approve audits, gates, or tools.
* Does not grant permissions.
* Does not override the verification entry point (`make -C ralph-workflow
  verify`).
