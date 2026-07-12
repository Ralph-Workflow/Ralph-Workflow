<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: documentation-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Documentation Policy

## Purpose and scope

This policy governs every documentation surface the project maintains:
user, operator, contributor, API, architecture, and code documentation.
It defines when a behaviour change requires a documentation update, the
expectations for docstrings, comments, public APIs, configuration,
commands, examples, migrations, and release notes, and where each kind
of documentation belongs.

## Default requirements

* Documentation MUST explain current behaviour and user decisions. It
  MUST NOT restate obvious code or include fabricated capabilities,
  dependencies, adoption claims, or unsupported technical statements.
* Behaviour changes MUST update affected documentation in the same
  workflow. Stale docs are a defect.
* The authoritative location for each kind of documentation MUST be
  documented in this policy. Stale, duplicated, contradictory, or
  obsolete documentation MUST be removed or reconciled.
* Examples and commands in documentation MUST match actual behaviour
  and MUST be verified where practical.
* Externally supported APIs MUST follow the declared documentation convention
  for purpose, parameters, return value, and relevant exceptions. Usage
  examples are required for non-obvious contracts, not trivial accessors.
* Configuration documentation MUST list every option with its
  default, valid range, and effect.
* Release notes MUST enumerate user-visible changes and required
  migrations.

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

RALPH-FACT: user_docs_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: operator_docs_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: contributor_docs_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: api_reference_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: architecture_docs_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: release_notes_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: docstring_convention: PROJECT-FACT-UNRESOLVED
RALPH-FACT: example_verification_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* UPDATE affected documentation in the same change that alters
  behaviour.
* REMOVE duplicated or contradictory documentation; do not silently
  duplicate.
* EXECUTE example commands when safe and feasible. Otherwise perform syntax
  or static validation and record dated review evidence, prerequisites, and
  the reason execution was unsafe or unavailable.
* RECORD the documentation review evidence appropriate to the change,
  covering the judgment execution cannot: accuracy against current behavior,
  absence of fabricated or unsupported claims, and authoritative-location
  reconciliation.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes a documentation location, the docstring
  convention, or the example verification command.

An agent MUST NOT:

* Fabricate capabilities, dependencies, adoption claims, or unsupported
  technical statements.
* Leave known drift between code and documentation for a later fix.
* Add duplicated copies of authoritative content.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: the RALPH-COMMAND gate covers only the mechanically
checkable slice — executing safe example commands and link/syntax
validation. Its first token must be an approved gate tool (wrap anything else
in `make`, `uv run`, or `npx`); if no such check exists yet, create the
smallest real one rather than a hollow command, or record a technically
justified RALPH-INAPPLICABLE line. Whether documentation is accurate,
non-fabricated, and free of obvious-code restatement is not script-checkable
— it is carried by the separate RALPH-REVIEW line, which you must always
resolve. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: review documentation for accuracy against current behavior, absence of fabricated or unsupported claims, and reconciliation of authoritative locations; evidence: dated documentation review or explicit not-performed blocker; owner: documentation owner

Command-gate success means every safe runnable example passes; every
non-runnable example has declared syntax/static validation or dated review
evidence, with its prerequisites, limitations, and reason for not executing
documented. The review gate certifies accuracy and non-fabrication.

## Exceptions

A documented exception (e.g. legacy doc kept for backward URL
compatibility) requires a documented rationale, scope, owner, and
removal or review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new documentation surface is added.
* The docstring convention changes.
* The example verification command changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Comment Quality"
  http: https://google.github.io/eng-practices/review/developer/
  review date: 2026-07-11

* publisher: Write the Docs
  title: "Documentation Style Guide"
  http: https://www.writethedocs.org/guide/writing/style-guides/
  review date: 2026-07-11

* publisher: The Twelve-Factor App
  title: "IX. Disposability"
  http: https://12factor.net/disposability
  review date: 2026-07-11

* publisher: Daniele Procida
  title: "Diátaxis Documentation Framework"
  http: https://diataxis.fr/
  review date: 2026-07-11

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

* Policy id: `<!-- ralph-policy-id: documentation-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
