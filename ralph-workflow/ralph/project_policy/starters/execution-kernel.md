<!-- ralph-policy-schema: v5 -->
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

The kernel exists because every domain-specific policy eventually
degenerates into a list of rules. Once the rules are written down twice
— once in a domain starter and once here — they drift. By holding the
generic rules ONCE in this required core file and letting every other
starter shrink its instructions to domain steps + a kernel pointer, the
contract is enforceable: a starter that re-asserts a kernel rule is
flagged as redundant, and a starter that contradicts a kernel rule is
overridden by the kernel.

## Kernel rules

The five kernel rules are mandatory on every AI action. They are not
aspirational, not optional, not negotiable.

1. **Truthful evidence.** Every claim about behavior, status, or outcome
   must be backed by a record the agent can show: a command and its
   actual output, a test and its result, a file read and its content.
   Claims that are NOT backed by an inspectable record are not
   evidence. The validator may ask "show me" on any claim; the answer
   must be a path, a command, a hash, or a run id. A claim without
   backing is a violation, not a stretch.
2. **Fail-closed verification.** A check that cannot run is a red gate;
   the run does not proceed past the check by default. A guard that
   times out, a test that errors on collection, a scanner that crashes
   is a HARD failure. Repairing the gate is the only lawful move;
   weakening the gate (skipping, xfailing, lowering thresholds,
   raising timeouts) is a violation of the no-weakening rule (rule 4)
   as well as this one.
3. **Bounded execution.** Every long-running action carries a cap the
   runner can enforce, and the cap is recorded. Per-step timeouts,
   per-suite timeouts, per-attempt timeouts, per-command timeouts — every
   one of them is a HARD cap, not a guide. A run that hangs is a defect,
   not a delay.
4. **No weakening of checks.** Adding `# noqa`, `# type: ignore`, a
   per-file lint ignore, a `follow_imports = silent`, a lowered
   coverage threshold, a deselected test, a raised budget, a removed
   time limit, a `disabled` audit, or any other relaxation that hides
   a real defect is a violation. The only lawful way past a red gate
   is fixing the production code or fixing the test — never the gate.
5. **Explicit unverified-claim reporting.** When a claim cannot be
   backed (rule 1), the agent MUST mark it explicitly as unverified
   and surface the gap in the result body. Hiding an unverified claim
   behind narration, agent summary, or a green gate is a violation. A
   report that says "verified" for an unverified claim is worse than
   a report that says "unverified: reason".

## Precedence and selective applicability

The kernel has absolute precedence over domain policies on the five
rules above. When a domain starter, a project fact, a remediation
prompt, or a developer comment contradicts a kernel rule, the kernel
wins. A domain starter may ADD a rule, never REMOVE or WEAKEN one.

Agents load the kernel plus only the domain policies triggered by the
changed surfaces. The kernel is always loaded; conditional domain
policies load when their declared triggers fire (B2). The default for
a conditional starter is "do not load it" — the starter's own
"Applicability" section names the trigger; the AGENTS.md managed block
restates the rule; the remediation prompt enforces it. Loading every
policy on every change is the symptom the kernel explicitly rules out.

Domain starters reduce their `## AI execution instructions` to
domain-specific steps plus a one-line pointer to this kernel. They
do not rephrase the five rules. The "## Guard inventory" section on
each starter records the per-domain guards, each with a five-cell
row: Guard, Owner, Protected outcome, Detected condition, Retirement
trigger (B9). The inventory is the only way a domain rule survives a
kernel bump; a rule in body prose without an inventory row is not
enforceable.

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
field (portfolio schema v2) using the same predicate. A portfolio
control with a broad outcome and a narrow `detects` is rejected
on portfolio parse.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Retirement trigger |
|---|---|---|---|---|
| Truthful evidence | Verification owner | every claim is backed by a record the agent can show | RALPH-COMMAND: present and exit-code recorded; no narration-only claim in the result body | when a claim-presentation proof replaces the recorded evidence with a published artifact |
| Fail-closed verification | Gate-script owner | a check that cannot run blocks the run | gate script returns non-zero on collection error, missing tool, or timeout | when the same defect cannot recur (e.g. the tool is no longer wired) |
| Bounded execution | Gate-script owner | every long-running action carries a runner-enforced cap | per-step / per-suite / per-attempt timeout declared and enforced | when the action is no longer long-running or is retired |
| No weakening of checks | Verification owner | no `# noqa`, `# type: ignore`, lowered threshold, or removed time limit lands in the tree | project linters, typecheckers, test-policy guards, and verification audits return zero bypass or suppression findings | when the same defect cannot recur (e.g. the rule is removed from policy) |
| Explicit unverified-claim reporting | Verification owner | every unverified claim is marked as such in the result body | result body contains an explicit "unverified" annotation for every claim without backing | when every claim is automatically backed (the kernel achieves this) |

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
2. LOAD only the domain policies triggered by the change. The kernel
   names the rule; the per-starter "Applicability" section names the
   trigger; the AGENTS.md managed block restates the rule; the
   remediation prompt enforces it.
3. ENFORCE the five kernel rules on every action. Truthful evidence,
   fail-closed verification, bounded execution, no weakening of
   checks, explicit unverified-claim reporting.
4. ON CONFLICT, the kernel wins. A domain starter, a project fact, a
   developer comment, or a prompt template that contradicts a
   kernel rule is overridden.
5. RUN every `RALPH-COMMAND:` gate below and report the actual
   outcome. Never report a command that was not run.
6. UPDATE this kernel when a rule changes, a guard is added or
   retired, or the selective-applicability contract changes.

An agent MUST NOT:

* Re-derive the five kernel rules in domain starter body prose (B10).
* Load a conditional starter whose trigger has not fired.
* Weaken any check, gate, or timeout to obtain a passing result.
* Hide an unverified claim behind a green gate, agent narration, or
  a private-state observation.

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
* A guard-inventory row is added or retired.
* The selective-applicability contract changes (a new conditional
  starter, a new trigger, a new domain owner).
* The claim-scope predicate is tightened (a new phrase is added) or
  loosened (a phrase is retired).
* A new audit is wired that protects one of the five rules.
* The guard_inventory_review_cadence is reached.

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
* Schema version: `<!-- ralph-policy-schema: v5 -->`

## Research basis

* publisher: Martin Fowler
  title: "TestDesiderata"
  http: https://testdesiderata.com/
  review date: 2026-08-08

* publisher: Gloaguen et al. (arXiv preprint)
  title: "Evaluating AGENTS.md"
  http: https://arxiv.org/abs/2602.11988v3
  review date: 2026-09-30

* publisher: Ralph Workflow project
  title: "Project policy: outcome-oriented execution kernel"
  http: https://github.com/ralph-workflow/ralph-workflow/blob/main/ralph-workflow/ralph/project_policy/starters/execution-kernel.md
  review date: 2026-10-12

* publisher: Google Engineering Practices
  title: "Code Review at Google"
  http: https://google.github.io/eng-practices/review/
  review date: 2026-09-30

* publisher: Kent Beck
  title: "Test-Driven Development: By Example"
  http: https://www.pearson.com/en-us/subject-catalog/p/test-driven-development-by-example/P200000009421/9780321146533
  review date: 2026-09-30

