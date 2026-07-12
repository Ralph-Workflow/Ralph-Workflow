<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: verification-policy.md -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point for the
project. It enumerates every gate that must pass before code can be
merged or released, the exact commands, the prerequisites, and the
bypass-detection rules.

## Default requirements

* A single authoritative verification entry point MUST exist (Makefile
  target, CI workflow, or `make verify` equivalent) that runs every
  declared gate in the documented order.
* Gates MUST include, as applicable to the project: tests, type
  checking, linting, formatting checks, policy enforcement scripts, and
  any other mandatory project gate.
* A gate documented here but not actually runnable is non-compliant.
  Documented impossibility MUST be reported as an active blocker.
* Bypass detection (lint/typecheck bypasses) MUST be enforced when the
  selected tools permit such checks. See "Bypass detection" below.

## Project facts to resolve

* RALPH-FACT: authoritative_verify_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: gate_prerequisites: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: gate_order: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: bypass_detection_lint_audit: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: bypass_detection_typecheck_audit: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ci_integration_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the project to identify the authoritative entry point (CI
  workflow file, Makefile target, etc.) before declaring gates.
* PRESERVE stricter existing verification rules; adapt rather than
  weaken.
* REPLACE every starter placeholder with a verified value.
* ENSURE every gate listed here is actually runnable in the
  environment. Document any gate that cannot run and the reason.
* RUN every declared `RALPH-COMMAND:` and report the outcome.

The agent MUST NOT:

* Add a "verification" command that does not exercise every gate.
* Weaken a gate to obtain a passing result.
* Hide bypasses via file-level disables or blanket silencers.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the authoritative entry
point. On failure, the agent MUST report the failing gate and the
failure category.

## Bypass detection

Lint and typecheck bypass detection MUST be enforced as part of the
authoritative verification gate. The bypass-detection rules:

* Newly weakened global configuration (per-file-ignores,
  ignore_missing_imports, follow_imports = silent, ignore_errors,
  disable_error_code, etc.) is detected and reported.
* Blanket or unexplained inline suppressions are detected and reported.
* Commands that claim to verify the project while omitting required
  paths are detected and reported.

The bypass-detection command MUST be declared as a `RALPH-COMMAND:`
under this heading. The audit tooling is project-specific; the agent
MUST wire the existing audit scripts into the verification gate.

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 (no bypass detected). On
failure, the agent MUST report the affected file, line, and bypass
category. Approved documented exceptions MUST be listed under
"Exceptions" below.

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

## Ralph markers

* Policy id: `<!-- ralph-policy-id: verification-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).