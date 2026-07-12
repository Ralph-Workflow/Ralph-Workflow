<!-- ralph-policy-schema: v1 -->
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
* Public APIs MUST have accurate docstrings covering: purpose,
  parameters, return value, raised exceptions, and a minimal usage
  example.
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
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
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
* VERIFY that every example command actually runs and produces the
  documented output.
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

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; only a gate that truly cannot exist may be recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

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
* Schema version: `<!-- ralph-policy-schema: v1 -->`
