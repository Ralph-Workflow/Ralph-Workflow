<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: verification-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point for the
project. It enumerates every gate that must pass before code can be
merged or released, the exact commands, the prerequisites, and the
bypass-detection rules.

## Default requirements

* A single authoritative pre-merge verification entry point MUST exist.
  Expensive platform, device, release, security, and scheduled gates MAY use
  named profiles when this policy states when each profile is mandatory.
* Gates MUST include, as applicable to the project: tests, type
  checking, linting, formatting checks, policy enforcement scripts, and
  any other mandatory project gate.
* Testing is mandatory for behavior-bearing software. Every language's
  maintained type-checking, linting, and formatting gates are mandatory
  when suitable tools exist. Preference, inconvenience, legacy findings,
  or missing setup does not make a supported gate inapplicable.
* A gate documented here but not actually runnable is non-compliant.
  Documented impossibility MUST be reported as an active blocker.
* Bypass detection MUST use native or existing checks when available.
  Custom tooling is required only when repository risk justifies it; a
  project MUST NOT create a hollow gate solely to satisfy this policy.

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

RALPH-FACT: authoritative_verify_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_prerequisites: PROJECT-FACT-UNRESOLVED
RALPH-FACT: gate_order: PROJECT-FACT-UNRESOLVED
RALPH-FACT: bypass_detection_lint_audit: PROJECT-FACT-UNRESOLVED
RALPH-FACT: bypass_detection_typecheck_audit: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_integration_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: required_verification_profiles: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* ENSURE every gate listed here is actually runnable in the
  environment. Document any gate that cannot run and the reason.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the authoritative entry point, gate order, or
  bypass-detection audit.

An agent MUST NOT:

* Add a "verification" command that does not exercise every gate.
* Weaken a gate to obtain a passing result.
* Hide bypasses via file-level disables or blanket silencers.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; only a gate that truly cannot exist may be recorded as
inapplicable with a reason and the condition that would create it.
You are FILLING OUT THIS FORM, not fixing the project: record the real
command and confirm it EXISTS (you MAY run it once as a bounded probe to
check that it resolves). Do NOT fix failing checks — type errors, failing
tests, lint findings, audit failures — and do NOT run a suite to green; a
failing or slow gate is the project's problem to address later, not a
form-filling blocker. Run only the commands you declare here, and if you
write a helper script to wire a gate, cover it with a unit test. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the authoritative entry
point. On failure, report the failing gate and the failure category.

## Bypass detection

Lint and typecheck bypass detection MUST be enforced as part of the
authoritative verification gate. The bypass-detection rules:

* Newly weakened global configuration (per-file-ignores,
  ignore_missing_imports, follow_imports = silent, ignore_errors,
  disable_error_code, etc.) is detected and reported.
* Blanket or unexplained inline suppressions are detected and reported.
* Commands that claim to verify the project while omitting required
  paths are detected and reported.

Declare a bypass-detection `RALPH-COMMAND:` when native/existing tooling or
demonstrated risk supports one. Otherwise declare `RALPH-INAPPLICABLE:` with
the tool limitation and the condition that would require an audit.

<!-- REPLACE-ME: set the real bypass-audit command when existing tooling or
demonstrated risk supports one; otherwise replace the line with a technically
justified RALPH-INAPPLICABLE declaration. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 (no bypass detected). On
failure, report the affected file, line, and bypass category. Approved
documented exceptions MUST be listed under "Exceptions" below.

## Exceptions

A documented bypass (e.g. a generated file with a `// @ts-nocheck`
header) requires a documented rationale, scope, owner, and removal or
review date. Undocumented bypasses are non-compliant.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new gate is added or an existing gate is removed.
* The authoritative entry point changes.
* The bypass-detection audit changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Speed of Code Reviews"
  http: https://google.github.io/eng-practices/review/reviewer/speed.html
  review date: 2026-07-11

* publisher: Google SRE Book
  title: "Monitoring Distributed Systems"
  http: https://sre.google/sre-book/monitoring-distributed-systems/
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Continuous Integration"
  http: https://martinfowler.com/articles/continuousIntegration.html
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

* Policy id: `<!-- ralph-policy-id: verification-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
