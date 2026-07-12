<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: linting-policy.md -->

# Linting Policy

## Purpose and scope

This policy governs the selected linters, their exact commands, the
source categories covered, the rule set, the strictness baseline, the
formatting responsibilities, and the rules around inline suppressions and
configuration overrides.

The maintained runtime is `ralph-workflow/ralph/` and its tests under
`ralph-workflow/tests/` (Python, ruff). The bundled Node.js artefact
`ralph-workflow/skills-package/` ships no linter; the Homebrew formula
`ralph-workflow/Formula/` is syntax-checked via Ruby. The retired
implementation under `docs/legacy-rust/` is quarantined. Each
detected language is evaluated below and recorded as either active or
inapplicable.

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
  suppressions are forbidden. Bare `# noqa` without a specific code is
  rejected by `ralph/testing/audit_lint_bypass.py`.
* File-level disables and per-file-ignores MUST be listed in this
  policy with the affected path and the rationale. Weakening
  per-file-ignores is detected by `ralph/testing/audit_lint_bypass.py`.

## Dead code — zero tolerance

Dead code is prohibited. It is better to delete obsolete code and implement
it again if demonstrated need returns than to retain unowned paths indefinitely.

* Unused, unreachable, obsolete, superseded, commented-out, and speculative
  code MUST be removed in the same change that makes it unnecessary.
* "Might be needed later," compatibility without a verified consumer, and
  incomplete future plans are never exceptions; version control preserves
  history.
* Dead-code findings MUST be fixed at the source, never hidden with ignores,
  exported-name tricks, fake references, coverage exclusions, or disabled rules.
  A proven live path reached through reflection, a framework/plugin registry,
  FFI callback, dynamic dispatch, conditional platform build, or external
  consumer MAY use the narrowest tool-supported live-entry annotation. This is
  not a dead-code exception: it requires observable consumer/entry-point
  evidence, rationale, owner, and review trigger; speculative consumers do not qualify.
* Generated or vendored code is governed at its source boundary. A retained
  first-party compatibility path requires a verified consumer, owner, removal
  condition, and expiry/review date.
* Every ecosystem-supported unused/dead-code check MUST run in the declared
  lint or verification gate. When no suitable maintained automated check
  exists, code review MUST still enforce deletion.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: linter_per_language: Python → ruff (`make lint` → `uv run ruff check ralph/ tests/`); Ruby → `make formula-check` runs `ruby -c Formula/ralph-workflow.rb`. Other languages detected in the stack are recorded in the Verification section below as inapplicable.
RALPH-FACT: rule_set_baseline: ruff rule set selected in pyproject.toml `[tool.ruff.lint].select`: E, F, W, I, N, UP, ANN, B, C4, SIM, RUF, TCH, PTH, PERF, PL, PGH (the Python-best-practice baseline under `line-length = 100`, `target-version = "py312"`). The per-file-ignores documented below are the only project-wide silencers, and they are anchored to specific files with explicit rationale.
RALPH-FACT: formatter_responsibility: ruff format (via `make fmt` → `uv run ruff format ralph/ tests/`); `make format-check` is the dry-run gate. Formatting is a separate `make verify` is not — `make verify` runs `ruff check` (lint), not `ruff format` (format). Formatters are not linters: a clean format-check is enforced via the documentation policy's example-verification command when the touched area needs it.
RALPH-FACT: excluded_paths: per-file-ignores in pyproject.toml `[tool.ruff.lint.per-file-ignores]` — `tests/**/*.py` (PLR2004, TC003, ANN001, ANN201, ANN202, ANN204, ANN401, E501, PLC0415 with documented rationale); `ralph/cli/**/*.py`, `ralph/config/**/*.py`, `ralph/display/**/*.py` (PLC0415 — lazy imports avoid circular dependencies); `ralph/**/*.py` (E501 — bounded-accumulator-ok / resource-lifecycle-ok markers on assignment lines, with rationale in the audit_lint_bypass allowlist); `ralph/mcp/explore/**/*.py` (PLC0415, PLR0911, PLR0912, PLR0915, RUF012, TC003); `ralph/mcp/tools/workspace/**/*.py` (PLC0415, TC003, PLR0911, PLR0912, PLR0915); `ralph/pipeline/**/*.py` (PLC0415 — lazy imports avoid the explore <-> workspace import cycle). Each block carries its rationale inline.
RALPH-FACT: suppression_rationale_policy: inline `# noqa: CODE` requires a specific ruff error code from the allowlist documented in `ralph/testing/audit_lint_bypass.py`. Blanket `# noqa` without a code, or a code outside the allowlist, is rejected. Audit allowlist entries are tracked with rationale and expiry.
RALPH-FACT: ci_gate_integration: `make verify` runs the ruff step as the first verify step (`ralph/verify.py:_VERIFY_STEPS`, label "ruff check ralph/ tests/"). Both Codeberg (Woodpecker) and GitHub (Actions) PR builds invoke `make verify` and fail the PR when ruff exits non-zero.
RALPH-FACT: maintenance_evidence: ruff (Astral, Apache-2.0, https://docs.astral.sh/ruff/) — actively maintained, latest stable >= 0.6, the single source of truth for Python linting and formatting in this project. Recheck on a major ruff version bump, Astral deprecating the project, or a Python release that drops compatibility with ruff's baseline. Same scrutiny applies to vulture (dead-code audit, MIT) — recheck on the next major release or a Python 3.13+ deprecation that affects it.
RALPH-FACT: first_party_languages: Python (primary), JavaScript (CommonJS in ralph-workflow/skills-package/, plain CommonJS with no linter wired), Ruby (a single Homebrew formula at ralph-workflow/Formula/ralph-workflow.rb checked by `make formula-check`). JSON and YAML are non-code and have no per-language linting.
RALPH-FACT: excluded_language_evidence: TypeScript / .ts and .tsx are absent (file extension scan returns zero); Go source is absent (no go.mod); Rust source is quarantined under docs/legacy-rust/ and is outside any build target. Each exclusion names the deterministic detector signal that triggered the inapplicability record, mirroring the same evidence used by the typecheck inapplicability record.
RALPH-FACT: dead_code_detection_per_language: Python -> `make -C ralph-workflow dead-code` runs `uv run --with vulture python -m vulture --config pyproject.toml` against the ralph package and tests (vulture is in dev extras, MIT, https://github.com/jendrikseipp/vulture); Ruby -> `make -C ralph-workflow formula-check` runs `ruby -c Formula/ralph-workflow.rb` (syntax-level only, since the file is a single build-distribution artefact); JavaScript and other detected languages have no dead-code audit wired because the only `.js` source is the skills-package distribution wrapper (treated as a distribution artefact). All audit findings are review-required: a finding is a defect to repair, not a threshold to raise.
RALPH-FACT: retained_compatibility_inventory: zero retained first-party compatibility paths. The legacy Rust pointer under docs/legacy-rust/ is a read-only historical pointer, not compiled into the project. The skills-package distribution wrapper is the single thin distribution layer and is rebuilt with each release. There are no shims, no deprecation re-export paths, and no fallback branches preserved "just in case"; deleted code is recovered via git, not via a retained compatibility layer.
RALPH-FACT: live_entry_point_annotation_inventory: zero live-entry annotations in current code. The only framework/plugin-registry / FFI-style entry points (Claude / OpenCode / Codex / Cursor / Pi stream parsers under ralph-workflow/ralph/agents/invoke.py, the MCP JSON envelope parser at ralph-workflow/ralph/mcp/server/_fallback_http_handler.py, and the subprocess NDJSON stream parsers under ralph-workflow/ralph/parsers/) are all imported eagerly by the ralph CLI and are reachable through the standard `python -m ralph` import graph — verified by the `import-graph` reachability check in ralph-workflow/ralph/agents/invoke.py. New live-entry annotations are accepted only when the entry point is a verified reflection / plugin / FFI / dynamic-dispatch surface with a documented consumer and removal trigger.

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

RALPH-LANG: Python
RALPH-COMMAND: make -C ralph-workflow lint

RALPH-LANG: TypeScript
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no TypeScript source exists in this project (no `tsconfig.json`, no `.ts` files); reopens when the first `.ts` file lands - at which point `RALPH-COMMAND: npx eslint .` or `RALPH-COMMAND: npx tsc --noEmit` becomes the gate.; evidence: ralph-workflow/language_detector returns zero `.ts` / `.tsx` files; ESLint and tsc are not installed; no tsconfig.json; owner: project-policy owner; expiry: 2027-07-12; warning: TypeScript linting is genuinely inapplicable until the first .ts file lands; review trigger: when a `.ts` file is added under the workspace root.

RALPH-LANG: Rust
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no Rust source is built in this project (docs/legacy-rust/ is the quarantined pointer to the retired Rust implementation and is outside any lint target); reopens when active Rust code is reintroduced - at which point `RALPH-COMMAND: cargo clippy --all-targets -- -D warnings` becomes the gate.; evidence: no Cargo.toml at repo root; docs/legacy-rust/ is marked retired in its README and excluded from any lint target; clippy is not installed; owner: project-policy owner; expiry: 2027-07-12; warning: Rust linting is genuinely inapplicable while the legacy pointer stays retired; review trigger: when a new Cargo.toml lands or a Rust module is reintroduced outside docs/legacy-rust/.

RALPH-LANG: Go
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no Go source exists in this project (no `go.mod`); reopens when the first Go file lands - at which point `RALPH-COMMAND: golangci-lint run ./...` becomes the gate.; evidence: no go.mod / go.sum at repo root; golangci-lint is not installed; ralph-workflow/language_detector scan reports zero `.go` files; owner: project-policy owner; expiry: 2027-07-12; warning: Go linting is genuinely inapplicable until a Go file lands; review trigger: when a go.mod lands at the workspace root.

RALPH-LANG: JavaScript
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no linter is wired up for the ralph-workflow/skills-package/ CommonJS Node.js artefact (no `.eslintrc*`, no `lint` script in skills-package/package.json); reopens when JS source gains a lint baseline - at which point `RALPH-COMMAND: npx eslint .` becomes the gate.; evidence: ralph-workflow/skills-package/ has no `.eslintrc*` and no lint script in its package.json; ESLint is not installed; the only `.js` files are the three plain CommonJS helpers in the skills-package wrapper; owner: project-policy owner; expiry: 2027-07-12; warning: JavaScript linting is genuinely inapplicable until ESLint is wired; review trigger: when a `.eslintrc*` or `lint` script is added to ralph-workflow/skills-package/.

RALPH-LANG: Ruby
RALPH-COMMAND: make -C ralph-workflow formula-check

The expected successful result is a clean lint run (exit 0). Lint
failures MUST be addressed at the source, not by adding suppressions.
For the Ruby formula, a clean `ruby -c` syntax check (the only Ruby
gate the project needs).

## Exceptions

A project that genuinely does not apply linting to a detected language
MUST mark that language with `RALPH-INAPPLICABLE:` and a documented
reason above. The reason must name the stack fact that would re-open
the question.

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
  review date: 2026-07-12

* publisher: ESLint Project
  title: "Configuring ESLint"
  http: https://eslint.org/docs/latest/use/configure/
  review date: 2026-07-12

* publisher: The Rust Project
  title: "Clippy documentation"
  http: https://doc.rust-lang.org/clippy/
  review date: 2026-07-12

* publisher: Google Engineering Practices
  title: "The CL Author's Guide to Getting Through Code Review"
  http: https://google.github.io/eng-practices/review/developer/
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

* Policy id: `<!-- ralph-policy-id: linting-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
