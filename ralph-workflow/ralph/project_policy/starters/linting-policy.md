<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: linting-policy.md -->

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

* RALPH-FACT: linter_per_language: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: rule_set_baseline: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: formatter_responsibility: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: excluded_paths: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: suppression_rationale_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ci_gate_integration: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the project to enumerate every language and any existing
  lint configuration before declaring coverage.
* PRESERVE stricter existing lint rules; adapt them rather than weaken.
* REPLACE every starter placeholder with a verified value.
* DECLARE one `RALPH-LANG:` block per language with the exact linter
  command.
* PREFER existing tooling. Adding a new linter requires a documented
  rationale.
* RUN every declared `RALPH-COMMAND:` and report the outcome.

The agent MUST NOT:

* Add `per-file-ignores`, `extend-per-file-ignores`, or any other
  global rule silencing without a per-file rationale.
* Use bare `# noqa` without a specific error code.
* Lower the lint strictness level to obtain a passing result.

## Verification

* RALPH-LANG: Python
* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

* RALPH-LANG: TypeScript
* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

* RALPH-LANG: Rust
* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

* RALPH-LANG: Go
* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

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
  title: "Code Health: Comment Style and Lint"
  http: https://google.github.io/eng-practices/review/developer/cl.html
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: linting-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: `the the project-policy-complete comment identifier comment` (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).