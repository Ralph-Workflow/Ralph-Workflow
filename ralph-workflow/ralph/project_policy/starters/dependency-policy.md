<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: dependency-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, deletes this banner, and adds the completion
marker. Readiness stays blocked while this banner or any placeholder token
remains. -->

# Dependency Policy

## Purpose and scope

This policy governs how every AI agent adds, updates, replaces, or
removes a runtime, development, or build-time dependency. It applies to
every change that modifies the package manager manifest (pyproject.toml,
package.json, Cargo.toml, go.mod, requirements.txt, etc.) or the
lockfile.

## Default requirements

* The agent MUST prefer dependencies with maintained, usable type
  information. Untyped dependencies must be stubbed at the type boundary
  (see the typechecking-policy.md).
* A small local implementation is preferred over a poorly maintained,
  untypeable, incompatible, or disproportionately large dependency. The
  "roll our own" decision MUST be justified by size, risk, or
  compatibility — never as a default.
* Lockfile-based reproducibility is mandatory. CI MUST install from
  the lockfile, never from a regenerated manifest.
* Security advisories MUST be tracked for every runtime dependency. A
  known critical CVE blocks the release unless explicitly waived in this
  policy.
* License compatibility MUST be verified against the project's
  distribution license. Incompatible licenses block the merge.
* Every dependency addition MUST be paired with a verification command
  declared in this policy.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: package_manager: PROJECT-FACT-UNRESOLVED
RALPH-FACT: lockfile_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: license_allowlist: PROJECT-FACT-UNRESOLVED
RALPH-FACT: security_audit_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: type_info_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_install_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* INSPECT the manifest and lockfile before adding a dependency.
* PREFER maintained, typed, well-licensed dependencies.
* AVOID adding a dependency for what a few lines of stdlib can express.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the package manager, lockfile format, license
  allowlist, or security audit command.

An agent MUST NOT:

* Add a dependency that lacks a maintainer, license, or recent release.
* Skip the lockfile update.
* Override the security audit command without a documented exception.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean dependency install + a clean
security audit (exit 0). On failure, report the affected package and
the failure category.

## Exceptions

A dependency with an incompatible license, unmaintained status, or
known critical CVE may be accepted with a documented rationale, scope
of the exception, owner of the exception, and a removal or review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new dependency is added.
* An existing dependency is updated, replaced, or removed.
* The package manager or lockfile format changes.
* The license allowlist or security audit command changes.

## Research basis

* publisher: OpenSSF (Linux Foundation)
  title: "Concise Guide for Evaluating Open Source Software"
  http: https://best.openssf.org/Concise-Guide-for-Evaluating-Open-Source-Software
  review date: 2026-07-11

* publisher: The Twelve-Factor App
  title: "XII. Admin Processes"
  http: https://12factor.net/admin-processes
  review date: 2026-07-11

* publisher: PyPA
  title: "Python Packaging User Guide: Managing Application Dependencies"
  http: https://packaging.python.org/en/latest/tutorials/managing-dependencies/
  review date: 2026-07-11

* publisher: OWASP Foundation
  title: "Top 10 Proactive Controls"
  http: https://owasp.org/www-project-proactive-controls/
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

* Policy id: `<!-- ralph-policy-id: dependency-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` comment; its presence
  certifies this file passed validation when it was last amended.