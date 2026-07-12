<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: clean-code-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, deletes this banner, and adds the completion
marker. Readiness stays blocked while this banner or any placeholder token
remains. -->

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
  An abstraction with one implementation and one caller is premature.
* Dead code, commented-out code, unused compatibility layers, and
  unused imports MUST be removed. The "we might need it later"
  rationale does not override the no-dead-code rule.
* Function, class, module, and file scope MUST follow single
  responsibility: a function does one thing, a module owns one
  concept.
* Error handling MUST surface actionable context. Bare `except: pass`
  is forbidden. Errors MUST be logged with enough context to diagnose.
* Code that cannot be tested cleanly MUST be refactored at the
  boundary, not patched with white-box tests.
* Generated code, vendored code, migration code, and compatibility
  code MUST be marked as such (via filename, header, or directory
  location) and excluded from generic lint/typecheck rules.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: naming_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: module_boundary_rule: PROJECT-FACT-UNRESOLVED
RALPH-FACT: error_handling_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: logging_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: generated_code_marker: PROJECT-FACT-UNRESOLVED
RALPH-FACT: vendored_code_marker: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dead_code_audit_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* PREFER existing project utilities and patterns over new abstractions.
* REMOVE dead code in the same change that obsoletes it.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the naming convention, module boundary rules,
  or error-handling and logging conventions.

An agent MUST NOT:

* Introduce speculative abstractions ("we might need it later").
* Add unused compatibility layers.
* Weaken the no-dead-code rule to obtain a passing result.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean dead-code audit. Report any
audit findings.

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

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between starter boilerplate and the project's established
  practice are resolved in favor of the existing project policy — adapt
  this file to the project, never the reverse.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: clean-code-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` comment; its presence
  certifies this file passed validation when it was last amended.
