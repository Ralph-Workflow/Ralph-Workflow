<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: clean-code-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Clean-Code Policy

## Purpose and scope

This policy governs project-specific naming, structure, module-boundary,
and readability rules. It defines the expectations for function, class,
module, and file scope and responsibility; the limits on dead code and
premature extensibility; the error-handling and logging conventions; the
rules for refactoring code that cannot be tested or understood cleanly;
and the treatment of generated, vendored, migration, and compatibility
code.

## Default requirements

* The agent MUST follow project-specific naming conventions verified
  against existing source. Generic style guides do NOT override the
  project's actual convention.
* The agent MUST prefer simple, maintainable solutions and existing
  project patterns over speculative abstractions or duplicated helpers.
  New abstractions require demonstrated boundary, volatility, testing, or
  reuse value; implementation count alone neither requires nor forbids one.
* Dead code, commented-out code, unused compatibility layers, and
  unused imports MUST be removed. The "we might need it later"
  rationale does not override the no-dead-code rule.
* Functions, classes, modules, and files MUST remain cohesive, expose
  understandable interfaces, and have bounded reasons to change.
* Error handling MUST surface actionable context. Bare `except: pass` is
  forbidden. Handle or propagate errors according to project convention;
  log once at the owning handling boundary and redact sensitive context.
* Test friction MUST prompt investigation. Refactor only when it reveals a
  real cohesion, dependency, or I/O-seam problem; do not create an artificial
  public seam solely for a test. Test at the narrowest useful observable
  boundary; never expose implementation or add wrappers to evade it
  (`execution-kernel.md`).
* Generated and vendored code MUST be identifiable and MAY be excluded from
  generic checks with a documented reason. First-party migration and
  compatibility code remain subject to applicable quality gates.
* Cross-component dependency direction, state ownership, and external I/O
  boundaries are governed by `architecture-policy.md`.

## Commenting policy

A comment records ONLY what the code cannot say: intent, a non-obvious
constraint, an invariant, a workaround with its removal trigger, or an
external contract. A comment that restates the code is noise, and a
comment that contradicts the code misleads the next maintainer.

Banned comment content:

* Restating the code the comment sits on.
* Change narration ("added for X", "fixed", "now", "previously").
* Commented-out code.
* Section banners.
* Notes addressed to the reviewer instead of to the next maintainer.
* A deferred-work marker (a `todo`, `fixme`, or `hack` prefix) that
  carries neither an owner nor a removal trigger.

Editing code means re-reading the adjacent comments: they may have gone
stale. Public-API docstrings stay mandatory (see
`documentation-policy.md`); this rule does not weaken that requirement.

Evidence tags differ by claim. The comment/code-inconsistency link to
defects is tagged moderate (the source is listed under Research basis)
and is the basis for a blocking check. The over-commenting claim — that
agents comment too much, and that removing such comments changes
outcomes — is tagged anecdotal and its effect size is unconfirmed, so a
comment that restates the code is reported for review and does not fail
the check; it is reviewed whenever the comment audit reports it. No
percentage, multiplier, or benchmark figure from either claim belongs in
this policy until it is confirmed against the primary source.

The inline bypass form is `# comment-ok: <reason>`; the reason is
mandatory, and an empty reason is not a bypass. The marker waives its
own line and the line immediately below it.

The comment audit runs in BASELINE MODE: it fails only on comments on
new or changed lines relative to the committed baseline, and it honours
the bypass above. It scans the source file types it is configured to
scan; comments in files it does not scan are covered by the review gate.
Burn-down of the existing baseline is a separate, optional follow-up
outside this gate; the remaining count comes from a one-off scan of the
committed tree, not from the gate.

## Guard inventory

<!-- REPLACE-ME: resolve each row's Enforcement column to 'deterministic: <command-id>'
matching a declared RALPH-COMMAND or 'advisory: <reason>; review: <trigger>'.
Then delete this comment. -->

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| Syntax-proxy ban | Clean-code owner | private-call ratchets and blanket functional rules do not reward making a method public or adding a wrapper | no private method is renamed to public solely to satisfy a private-call ratchet; no wrapper exists only to satisfy a blanket functional rule | no private method is renamed to public solely to satisfy a private-call ratchet | deterministic: PROJECT-FACT-UNRESOLVED | when every private-call ratchet is retired |
| Bounded premature abstraction | Clean-code owner | new abstractions are justified by demonstrated boundary, volatility, testing, or reuse value rather than implementation count | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value; implementation count alone neither requires nor forbids abstraction | abstraction is justified by demonstrated boundary, volatility, testing, or reuse value | deterministic: PROJECT-FACT-UNRESOLVED | when the project stops growing the abstraction target |
| Comment content | Clean-code owner | the comments on a new or changed line carry what the code cannot say | a comment on a new or changed line matches a blocking banned comment shape listed under Commenting policy | a comment on a new or changed line matches a blocking banned comment shape | deterministic: PROJECT-FACT-UNRESOLVED | when the comment gate is retired |

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires. Then
delete this comment. -->

RALPH-FACT: naming_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: module_boundary_rule: PROJECT-FACT-UNRESOLVED
RALPH-FACT: error_handling_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: logging_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: generated_code_marker: PROJECT-FACT-UNRESOLVED
RALPH-FACT: vendored_code_marker: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dead_code_audit_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

This section is the clean-code-specific procedure only. The five
generic rules (truthful evidence, fail-closed verification, bounded
execution, no weakening of checks, explicit unverified-claim
reporting), the precedence over domain policies, the
selective-applicability rule, and the claim-scope rule are owned by
the kernel starter (`execution-kernel.md`) and MUST be loaded from
there — do not re-derive them here. Exposing a private method or
adding a wrapper solely to satisfy a test or a structural proxy rule
is a violation (see `testing-policy.md`, `architecture-policy.md`);
the same mechanism is never justified here as a clean-code fix.

To follow this policy, an agent making any change MUST:

* PREFER existing project utilities and patterns over new abstractions.
* REMOVE dead code in the same change that obsoletes it.
* RECORD the clean-code review evidence appropriate to the change,
  covering the judgment a dead-code audit cannot: naming, cohesion,
  interface clarity, and abstraction justification.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the naming convention, module boundary rules,
  or error-handling and logging conventions.

An agent MUST NOT:

* Introduce speculative abstractions ("we might need it later").
* Add unused compatibility layers.
* Weaken the no-dead-code rule to obtain a passing result.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: the RALPH-COMMAND gates cover only the mechanically
checkable slice — the first RALPH-COMMAND is the dead-code / unused-import
audit named in `dead_code_audit_command`, and the second is the comment
audit for the Commenting policy rule. Each command's first token must be
an approved gate tool (wrap anything else in `make`, `uv run`, or `npx`);
if no such audit exists yet, create the smallest real one rather than a
hollow command, or record a
technically justified RALPH-INAPPLICABLE line. Naming, cohesion, interface
clarity, and abstraction judgment are not script-checkable — they are carried
by the separate RALPH-REVIEW line, which you must always resolve. Then delete
this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review naming, cohesion, interface clarity, abstraction justification, comment content, and error/logging conventions against verified project convention; evidence: dated clean-code review or explicit not-performed blocker; owner: code quality owner

<!-- REPLACE-ME: set the project's real comment audit command. It reports the
banned comment shapes listed under Commenting policy on the lines a change
touched, and it honours the inline `# comment-ok: <reason>` bypass. Prefer a
check the project already runs over new tooling; if the project has no such
audit yet, create the smallest real one rather than declaring a hollow
command. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The command gates' expected successful results are a clean dead-code audit
and a clean comment audit over the new and changed lines; report any audit
findings. The review gate certifies the judgment the audits cannot.

## Exceptions

A documented exception (e.g. legacy compatibility shim with a removal
date) requires a documented rationale, scope, owner, and removal or
review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new naming convention is introduced.
* A new module boundary rule is introduced.
* The error-handling or logging convention changes.
* A new generated/vendored code category is added.
* The commenting rule changes: a new banned comment shape, a change to
  the bypass form, or a change to the baseline-mode scope.

## Research basis

* publisher: Robert C. Martin ("Uncle Bob")
  title: "The Clean Code Blog"
  http: https://blog.cleancoder.com/
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Refactoring: Improving the Design of Existing Code"
  http: https://martinfowler.com/books/refactoring.html
  review date: 2026-07-11

* publisher: Google Engineering Practices
  title: "Code Health: Reduce Nesting, Reduce Complexity"
  http: https://google.github.io/eng-practices/review/developer/
  review date: 2026-07-11

* publisher: Sandi Metz
  title: "Practical Object-Oriented Design in Ruby"
  http: https://www.poodr.com/
  review date: 2026-07-11

* publisher: Microsoft Press / Steve McConnell
  title: "Code Complete: A Practical Handbook of Software Construction"
  http: https://www.microsoftpressstore.com/articles/article.aspx?p=2222451
  review date: 2026-07-12

* publisher: arXiv (Radmanesh, Imani, Ahmed, Moshirpour)
  title: "Investigating the Impact of Code Comment Inconsistency on Bug Introducing"
  http: https://arxiv.org/abs/2409.10781
  review date: 2026-11-09

* publisher: Google Style Guides
  title: "Documentation Best Practices"
  http: https://google.github.io/styleguide/docguide/best_practices.html
  review date: 2026-11-09

## Portfolio ownership

Shared admission, composition, aggregate-budget, evidence, exception, and
lifecycle rules are owned by `policy-portfolio.toml`; they are not repeated
here. This file owns only its domain-specific observable outcomes, verified
facts, commands, and review triggers. Amendments update the effective portfolio
while its mandatory outcomes remain protected.

## Living document contract

This policy is a living document. Keep its domain-specific facts, commands,
requirements, and review triggers aligned with verified project reality.
Shared amendment admission and retention are governed once by
`policy-portfolio.toml`; generic rule protection and non-weakening are
governed by `execution-kernel.md §Kernel rules`.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: clean-code-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v6 -->`
