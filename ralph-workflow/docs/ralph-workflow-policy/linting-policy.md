<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: linting-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Linting Policy

## Purpose and scope

This policy governs the selected linters, their exact commands, the
source categories covered, the rule set, the strictness baseline, the
formatting responsibilities, and the rules around inline suppressions and
configuration overrides.

## Default requirements

* The selected linter MUST match the language ecosystem best practice
  (ruff for Python, eslint for JavaScript/TypeScript, clippy for Rust,
  golangci-lint for Go, etc.). A minimal configuration chosen only to
  pass existing code is forbidden; the baseline MUST be the
  established best-practice rule set for the language.
* Per-language `RALPH-LANG:` declarations are required for every
  detected language, each followed by a `RALPH-COMMAND:` line or an
  explicit `RALPH-INAPPLICABLE:` line.
* Inline suppressions (`# noqa`, `// eslint-disable`, `#[allow(...)]`)
  MUST carry a specific error code AND a documented rationale. Blanket
  suppressions are forbidden.
* File-level disables and per-file-ignores MUST be listed in this
  policy with the affected path and the rationale.

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

RALPH-FACT: linter_per_language: PROJECT-FACT-UNRESOLVED
RALPH-FACT: rule_set_baseline: PROJECT-FACT-UNRESOLVED
RALPH-FACT: formatter_responsibility: PROJECT-FACT-UNRESOLVED
RALPH-FACT: excluded_paths: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suppression_rationale_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ci_gate_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* DECLARE one `RALPH-LANG:` block per language with the exact linter
  command.
* PREFER existing tooling. Adding a new linter requires a documented
  rationale.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the linter, rule set, formatter, or
  suppression policy.

An agent MUST NOT:

* Add `per-file-ignores`, `extend-per-file-ignores`, or any other
  global rule silencing without a per-file rationale.
* Use bare `# noqa` without a specific error code.
* Lower the lint strictness level to obtain a passing result.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: per-language template. Keep one block per project language
with the real linter command (first token must be an approved gate tool;
wrap others in `make`, `uv run`, or `npx`), add blocks for detected
languages missing below, drop blocks for languages the project does not
use, and record genuinely unlinted languages as inapplicable with a
reason. Then delete this comment. -->

RALPH-LANG: Python
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: TypeScript
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Rust
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

RALPH-LANG: Go
RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean lint run (exit 0). Lint
failures MUST be addressed at the source, not by adding suppressions.

## Exceptions

A project that genuinely does not apply linting to a detected language
MUST mark that language with `RALPH-INAPPLICABLE:` and a documented
reason.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new language is added to the project.
* The linter, version, or rule set changes.
* A new lint dependency is added.
* The suppression policy changes.

## Research basis

* publisher: Astral (ruff)
  title: "ruff documentation: Rules"
  http: https://docs.astral.sh/ruff/rules/
  review date: 2026-07-11

* publisher: ESLint Project
  title: "Configuring ESLint"
  http: https://eslint.org/docs/latest/use/configure/
  review date: 2026-07-11

* publisher: The Rust Project
  title: "Clippy documentation"
  http: https://doc.rust-lang.org/clippy/
  review date: 2026-07-11

* publisher: Google Engineering Practices
  title: "The CL Author's Guide to Getting Through Code Review"
  http: https://google.github.io/eng-practices/review/developer/
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

* Policy id: `<!-- ralph-policy-id: linting-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`