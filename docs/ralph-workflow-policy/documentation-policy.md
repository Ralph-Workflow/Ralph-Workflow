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
  example. A non-empty module docstring is enforced by
  `ralph.testing.audit_public_docstrings` for every public module
  under `ralph/`.
* Configuration documentation MUST list every option with its
  default, valid range, and effect.
* Release notes MUST enumerate user-visible changes and required
  migrations. The CHANGELOG is the canonical home.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: user_docs_path: README.md + START_HERE.md + docs/README.md (and the Sphinx operator manual at ralph-workflow/docs/sphinx/index.rst). The user-facing store-front is README.md at the repo root; the operator manual is the Sphinx build, regenerated with `make -C ralph-workflow docs`.
RALPH-FACT: operator_docs_path: ralph-workflow/docs/sphinx/ (Sphinx HTML build; the canonical operator manual) plus the per-area pages in ralph-workflow/docs/sphinx/ (configuration.md, cli.md, troubleshooting.md, recovery.md, artifacts.md, agents.md, agent-compatibility.md, mcp-tools.md, mcp-tool-restriction.md, versioning.md, pro-support.md, advanced-*.md).
RALPH-FACT: contributor_docs_path: CONTRIBUTING.md (root pointer) → ralph-workflow/CONTRIBUTING.md (maintained guide) plus the contributor-facing subpages in ralph-workflow/docs/agents/ (verification.md, testing-guide.md, artifact-submission-contract.md, type-ignore-policy.md, memory-lifecycle.md, adding-a-new-agent.md, quickstart-add-a-new-agent.md) and the routing pages in docs/agents/ at the repo root.
RALPH-FACT: api_reference_path: the Sphinx API reference is auto-generated from `ralph/.. automodule::` entries in ralph-workflow/docs/sphinx/modules.rst (enforced by tests/test_sphinx_modules_coverage.py). New public modules under `ralph/` MUST add an `.. automodule::` entry in the same commit.
RALPH-FACT: architecture_docs_path: docs/architecture/ (repo-root architecture index) and ralph-workflow/docs/architecture/ (package architecture). MADR-format ADRs live under ralph-workflow/docs/architecture/adr-*.md; one example is adr-0001-interrupt-architecture.md.
RALPH-FACT: release_notes_path: ralph-workflow/CHANGELOG.md (canonical) plus the per-version Sphinx changelog rollup if a release note spans multiple pages. The CHANGELOG follows the Keep a Changelog + SemVer convention; every user-visible change has a CHANGELOG entry in the same workflow that lands the change.
RALPH-FACT: docstring_convention: PEP 257 + Google-style sections (Args:, Returns:, Raises:, Example:) on every public function / class / module. Module docstrings are non-empty for every module under `ralph/` (enforced by `ralph.testing.audit_public_docstrings`). Sphinx `.. automodule::` directives render the same docstrings into the operator manual; a docstring edit updates both surfaces at once.
RALPH-FACT: example_verification_command: `make -C ralph-workflow docs-linkcheck` runs Sphinx's link-check over the operator manual (`uv run --extra docs sphinx-build -b linkcheck docs/sphinx docs/sphinx/_build/linkcheck`). For non-Sphinx route pages (README.md, START_HERE.md, docs/README.md, ralph-workflow/docs/README.md) the project ships `scripts/check_route_page_links.py` (head + GET, 10 s per-request timeout, http://PROMPT.md and docs.claude.com excluded per ralph-workflow/docs/sphinx/conf.py). Both run as part of the maintainer's release preflight.

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
  technical statements. The fabrication guard
  (`scripts/fabrication_guard.py` level 1/2/3) is the authoritative
  detector for public-facing markdown.
* Leave known drift between code and documentation for a later fix.
* Add duplicated copies of authoritative content.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow docs

This is the Sphinx HTML build (via `uv run --extra docs sphinx-build
-b html docs/sphinx docs/sphinx/_build/html -W --keep-going`); it
fails the gate on any Sphinx warning and is wired in as a Make
prerequisite of `make verify` so the build runs before the Python
verify step.

The expected successful result is that every documented command
actually runs and produces the documented output. The optional
linkcheck (`make -C ralph-workflow docs-linkcheck`) catches broken
external URLs in the operator manual on demand.

For non-Sphinx route pages:

RALPH-COMMAND: python3 scripts/check_route_page_links.py README.md START_HERE.md docs/README.md ralph-workflow/docs/README.md

This is the route-page link checker; failures list the affected
file/line and the broken URL.

## Exceptions

A documented exception (e.g. legacy doc kept for backward URL
compatibility) requires a documented rationale, scope, owner, and
removal or review date. The exception lives in this section; the
surface itself carries a marker cross-referencing the entry.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new documentation surface is added.
* The docstring convention changes.
* The example verification command changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Comment Quality"
  http: https://google.github.io/eng-practices/review/developer/
  review date: 2026-07-12

* publisher: Write the Docs
  title: "Documentation Style Guide"
  http: https://www.writethedocs.org/guide/writing/style-guides/
  review date: 2026-07-12

* publisher: The Twelve-Factor App
  title: "IX. Disposability"
  http: https://12factor.net/disposability
  review date: 2026-07-12

* publisher: Daniele Procida
  title: "Diátaxis Documentation Framework"
  http: https://diataxis.fr/
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

* Policy id: `<!-- ralph-policy-id: documentation-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
