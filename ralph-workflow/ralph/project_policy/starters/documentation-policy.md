<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: documentation-policy.md -->

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
* Public APIs MUST have accurate docstrings covering: purpose,
  parameters, return value, raised exceptions, and a minimal usage
  example.
* Configuration documentation MUST list every option with its
  default, valid range, and effect.
* Release notes MUST enumerate user-visible changes and required
  migrations.

## Project facts to resolve

* RALPH-FACT: user_docs_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: operator_docs_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: contributor_docs_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: api_reference_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: architecture_docs_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: release_notes_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: docstring_convention: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: example_verification_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the project's documentation tree to identify authoritative
  locations before editing.
* PRESERVE stricter existing documentation rules; adapt rather than
  weaken.
* REPLACE every starter placeholder with a verified value.
* UPDATE affected documentation in the same change that alters
  behaviour.
* REMOVE duplicated or contradictory documentation; do not silently
  duplicate.
* VERIFY that every example command actually runs and produces the
  documented output.
* RUN every declared `RALPH-COMMAND:` and report the outcome.

The agent MUST NOT:

* Fabricate capabilities, dependencies, adoption claims, or unsupported
  technical statements.
* Leave known drift between code and documentation for a later fix.
* Add duplicated copies of authoritative content.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is that every documented command
actually runs and produces the documented output.

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

* Conflicts between starter boilerplate and the project's established
  practice are resolved in favor of the existing project policy — adapt
  this file to the project, never the reverse.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: documentation-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).