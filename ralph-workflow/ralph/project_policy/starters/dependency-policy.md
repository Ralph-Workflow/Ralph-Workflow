<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: dependency-policy.md -->

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

* RALPH-FACT: package_manager: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: lockfile_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: license_allowlist: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: security_audit_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: type_info_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ci_install_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the manifest and lockfile before adding a dependency.
* PRESERVE stricter existing dependency rules; adapt rather than weaken.
* REPLACE every starter placeholder with a verified value.
* PREFER maintained, typed, well-licensed dependencies.
* AVOID adding a dependency for what a few lines of stdlib can express.
* RUN every declared `RALPH-COMMAND:` and report the outcome.

The agent MUST NOT:

* Add a dependency that lacks a maintainer, license, or recent release.
* Skip the lockfile update.
* Override the security audit command without a documented exception.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean dependency install + a clean
security audit (exit 0). On failure, the agent MUST report the affected
package and the failure category.

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
  http: https://openssf.org/resources/guides/evaluating-open-source-software/
  review date: 2026-07-11

* publisher: The Twelve-Factor App
  title: "XII. Admin Processes"
  http: https://12factor.net/admin-processes
  review date: 2026-07-11

* publisher: PyPA
  title: "Python Packaging User Guide: Dependency Management"
  http: https://packaging.python.org/en/latest/discussions/dependency-management/
  review date: 2026-07-11

* publisher: OWASP Foundation
  title: "Top 10 Proactive Controls"
  http: https://owasp.org/www-project-proactive-controls/
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: dependency-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).