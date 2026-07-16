<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: dependency-policy.md -->

# Dependency Policy

## Purpose and scope

This policy governs how every AI agent adds, updates, replaces, or
removes a runtime, development, or build-time dependency. It applies to
every change that modifies the package manager manifest
(`ralph-workflow/pyproject.toml`, `ralph-workflow/skills-package/package.json`)
or the lockfile (`ralph-workflow/uv.lock`).

The maintained runtime is a Python package; the bundled Node.js artefact
under `ralph-workflow/skills-package/` is a thin distribution wrapper for
the mirrored Superpowers skills content and is pinned in source control.

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

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: package_manager: uv (primary; `[project]` dependencies and `[project.optional-dependencies]` for dev/docs/web-search/bundle extras in ralph-workflow/pyproject.toml). npm is used only inside ralph-workflow/skills-package/ for the bundled skills distribution artefact (one CLI bin at ralph-workflow/skills-package/bin/skills.js, two helper scripts; no runtime dependencies, no test/lint toolchain).
RALPH-FACT: lockfile_path: ralph-workflow/uv.lock (uv-managed; pinned by `uv sync`). The skills-package artefact does not produce a package-lock.json because it ships zero runtime dependencies.
RALPH-FACT: license_allowlist: project distribution license is `AGPL-3.0-or-later` (per ralph-workflow/pyproject.toml `[project].license`). Dependencies MUST use OSI-approved permissive or copyleft licenses compatible with AGPL-3.0-or-later distribution: MIT, BSD-2/3-Clause, Apache-2.0, ISC, MPL-2.0, LGPL-2.1-or-later, LGPL-3.0-or-later, PSF-2.0, and similar. Non-OSI / source-available licenses (BSL, SSPL, Elastic, BUSL) MUST NOT be merged without a documented exception naming the license, the scope, the owner, and the review date. GPL-family (other than LGPL) is incompatible with downstream proprietary linking without an explicit dual-license strategy.
RALPH-FACT: security_audit_command: Bandit (the Python-best-practice static analyzer; it is the language-scoped scanner called out in security-policy.md). Bandit is NOT currently pinned in `ralph-workflow/pyproject.toml`, NOT installed by `make dev`, and NOT wired into `make -C ralph-workflow verify`; the Verification section below records the corresponding deferred gate. A broader CVE audit depends on the GitHub Advisory Database (Dependabot) and is wired through CODEOWNERS + Renovate rather than a CLI command.
RALPH-FACT: type_info_policy: every added Python dependency MUST either ship type information in its own wheel (the default for httpx, pydantic, typer, rich, mcp, loguru, tqdm, gitpython, sentry-sdk, watchdog, jinja2, readability-lxml, selectolax) or carry a companion types package declared in `[project.optional-dependencies].dev` (`types-psutil>=5.9` is the canonical example for the untyped `psutil` runtime dep). Pure-stdlib reimplementations are preferred over dependencies that drag type stubs across the dep graph.
RALPH-FACT: ci_install_command: `make dev` (declared in ralph-workflow/Makefile as `uv sync --extra dev`). CI installs from the lockfile by running the same target; `--extra` is `--frozen`-safe because uv resolves against `uv.lock` for every extra in the sync.
RALPH-FACT: dependency_evaluation_record: each existing runtime dependency is recorded in ralph-workflow/pyproject.toml `[project].dependencies` and was evaluated against four criteria: (1) the upstream is actively maintained (last release within 18 months), (2) the wheel carries type information (or a sibling `types-*` package is declared in dev extras), (3) the license is on the ralph-workflow-pyproject AGPL-3.0-or-later compatible list (MIT / BSD / Apache-2.0 / ISC / MPL-2.0 / PSF / LGPL), and (4) the dependency is the smallest maintained option that satisfies the bound API. Recorded dependencies include httpx (HTTP client), pydantic (data model), typer + rich-click (CLI), rich (terminal), mcp (MCP transport), loguru (logging), tqdm (progress bars), gitpython (git ops), sentry-sdk (error reporting, opt-in), watchdog (filesystem events), jinja2 (templating), readability-lxml + selectolax (HTML extraction). No BSL/SSPL/Elastic/BUSL/non-OSI licence is present.
RALPH-FACT: transitive_dependency_policy: transitive dependencies are governed by the lockfile (`ralph-workflow/uv.lock`), not by direct inspection. Every transitive is recorded with a pinned version, a sha256 hash, and an indirect provenance back to a top-level entry in `[project].dependencies`. CI runs `uv sync --frozen --extra dev` so the lockfile is the sole source of truth at install time. A transitive that is not pinned in the lockfile is a build failure, never a runtime surprise. Adding a new top-level dependency requires the four-criteria evaluation in `dependency_evaluation_record`; no transitive is added directly (it MUST come in through a top-level resolution).

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
* Add a non-OSI / source-available license (BSL, SSPL, BUSL, Elastic)
  without a documented exception with license, scope, owner, and review
  date.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow dev

This is the canonical dependency install gate from the lockfile; CI
runs it as the env-setup step and fails the build if the lockfile is
out of sync with `pyproject.toml`.

The expected successful result is a clean dependency install (exit 0).
On failure, report the affected package and the failure category
(resolver conflict, missing platform wheel, network, lockfile drift).
A clean Bandit scan for added Python source is reported alongside:

RALPH-PENDING: uv run bandit -q -r ralph/ (assumed 2026-07-15); review trigger: once bandit is pinned as a dev dependency in `ralph-workflow/pyproject.toml` and a verify step is added to `ralph-workflow/ralph/verify.py:_VERIFY_STEPS` and wired into `make -C ralph-workflow verify`.

Failures here would be security findings, not dependency conflicts;
triage each finding under the security-policy.md Exceptions section.

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
  review date: 2026-07-12

* publisher: The Twelve-Factor App
  title: "XII. Admin Processes"
  http: https://12factor.net/admin-processes
  review date: 2026-07-12

* publisher: PyPA
  title: "Python Packaging User Guide: Managing Application Dependencies"
  http: https://packaging.python.org/en/latest/tutorials/managing-dependencies/
  review date: 2026-07-12

* publisher: OWASP Foundation
  title: "Top 10 Proactive Controls"
  http: https://owasp.org/www-project-proactive-controls/
  review date: 2026-07-12

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
