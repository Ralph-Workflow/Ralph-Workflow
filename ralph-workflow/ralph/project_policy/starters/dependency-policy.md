<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: dependency-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Dependency Policy

## Purpose and scope

This policy governs how every AI agent adds, updates, replaces, or
removes a runtime, development, or build-time dependency. It applies to
every change that modifies the package manager manifest (pyproject.toml,
package.json, Cargo.toml, go.mod, requirements.txt, etc.) or the
lockfile.

## Default requirements

* The agent MUST prefer dependencies with maintained, usable type
  information. When unavailable, unchecked values MUST be contained at a
  typed or validated boundary using adapters, protocols, validation, stubs,
  or another checker-supported mechanism; blanket silencing is forbidden.
* A small local implementation is preferred over a poorly maintained,
  untypeable, incompatible, or disproportionately large dependency. The
  "roll our own" decision MUST be justified by size, risk, or
  compatibility — never as a default.
* Deployable applications and artifacts MUST use reproducible dependency
  resolution. Published libraries MUST document how CI verifies supported
  dependency ranges and whether lockfiles govern development, release, or both.
* Security advisories MUST be tracked for every runtime dependency. A
  known critical CVE blocks the release unless explicitly waived in this
  policy.
* License compatibility MUST be verified against the project's
  distribution license. Incompatible licenses block the merge.
* Every dependency addition MUST be covered by the declared install, build,
  test, license, and security gates that apply to its risk and project role.

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

RALPH-FACT: package_manager: PROJECT-FACT-UNRESOLVED
RALPH-FACT: lockfile_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: license_allowlist: PROJECT-FACT-UNRESOLVED
RALPH-FACT: security_audit_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: type_info_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_install_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: dependency_evaluation_record: PROJECT-FACT-UNRESOLVED
RALPH-FACT: transitive_dependency_policy: PROJECT-FACT-UNRESOLVED

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

* Add a dependency without assessing maintenance, provenance, license,
  vulnerabilities, compatibility, and footprint. Release recency alone is
  neither proof of health nor a reason to reject a stable dependency.
* Skip the required resolution or compatibility update for the project type.
* Override the security audit command without a documented exception.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet (for example the tool is
not installed on a new project) is recorded as a RALPH-PENDING deferral —
`RALPH-PENDING: <approved-tool> (assumed <date>); review trigger: <trigger>`
— which reaches readiness and is resolved by a later dev cycle when its
trigger fires; only a gate that truly cannot EVER exist is recorded as
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

The expected successful result is a clean dependency install + a clean
security audit (exit 0). On failure, report the affected package and
the failure category.

## Exceptions

An unmaintained dependency or known critical vulnerability requires explicit,
time-bounded risk acceptance, mitigation, qualified owner, and removal/review
date. A license incompatibility cannot be waived as an engineering exception:
it remains blocked unless qualified legal/license review establishes and
records a compliant distribution path, reviewing authority, and compliance
basis, such as permission, relicensing, replacement, or a changed distribution
model.

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
* Schema version: `<!-- ralph-policy-schema: v2 -->`
