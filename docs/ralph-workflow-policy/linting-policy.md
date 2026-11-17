<!-- ralph-policy-schema: v6 -->
<!-- ralph-policy-id: linting-policy.md -->

# Linting Policy

## Purpose and scope

This policy governs the selected linters, their exact commands, the
source categories covered, the rule set, the strictness baseline, the
formatting responsibilities, and the rules around inline suppressions and
configuration overrides.

The maintained runtime is `ralph-workflow/ralph/` and its tests under
`ralph-workflow/tests/` (Python, ruff). The Homebrew formula
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
* Inline suppressions are forbidden. The underlying lint warning is fixed
  in code, or suppressed in tool configuration (`pyproject.toml`
  `[tool.ruff.lint.per-file-ignores]`), never inline.
* File-level disables and per-file-ignores MUST be listed in this
  policy with the affected path and the reason. Weakening
  per-file-ignores is detected by `ralph/testing/audit_lint_bypass.py`.
* The lint configuration starts by enabling every rule and disables only a
  rule that fires on common safe use. It contains no per-file ignore of a
  banned rule, no `extend-ignore` that re-enables a banned rule, and no inline
  suppression marker. Explicit `decision` under the criteria's "common safe use" clause:
  * `TRY003`: per-file ignore on `ralph/**/*.py` and `tests/**/*.py` because descriptive exception strings at raise sites are standard safe practice and custom exception classes for every site would introduce unnecessary complexity.
  * `TRY300`: per-file ignore on `ralph/**/*.py` because direct returns within try blocks preserve local flow control without unnecessary else-nesting.
  * `FBT001`, `FBT002`, `FBT003`: per-file ignore on `ralph/**/*.py` (FBT001, FBT002) and `tests/**/*.py` (FBT001, FBT002, FBT003) because boolean positional arguments and fixtures in tests and established API contracts represent common safe usage where keyword-only migration would break caller compatibility.

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
  evidence, reason, owner, and review trigger; speculative consumers do not qualify.
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

RALPH-FACT: linter_per_language: Python → ruff (`make lint` → `uv run ruff check ralph/ tests/`); Ruby → `make formula-check` runs `ruby -c Formula/ralph-workflow.rb` and fails closed with an actionable gate-policy citation when Ruby is unavailable. `make verify` requires this target; CI provisions Ruby before verification. Other languages detected in the stack are recorded in the Verification section below as inapplicable.
RALPH-FACT: rule_set_baseline: ruff rule set selected in pyproject.toml `[tool.ruff.lint].select`: E, F, W, I, N, UP, ANN, B, C4, SIM, RUF, TCH, PTH, PERF, PL, PGH, ASYNC251, S, TRY, BLE, FBT, TC006 (the Python-best-practice baseline under `line-length = 100`, `target-version = "py312"`). The per-file-ignores documented below are the only project-wide silencers, and they are anchored to specific files with explicit reason.
RALPH-FACT: formatter_responsibility: ruff format (via `make fmt` → `uv run ruff format ralph/ tests/`); `make format-check` is the dry-run gate. Formatting is a separate concern from linting: `make verify` runs `ruff check` (lint), not `ruff format` (format). Formatters are not linters; a developer who touches Python files under `ralph/` or `tests/` and wants a verification of formatting cleanliness invokes `make format-check` directly (it exits non-zero when `ruff format --check` reports reformat-required files). The doc-route link checks (`make docs-linkcheck` and `make route-linkcheck`) are also opt-in preflight commands documented in the documentation policy and are not part of `make verify`.
RALPH-FACT: excluded_paths: per-file-ignores in pyproject.toml `[tool.ruff.lint.per-file-ignores]` — the complete inventory of keys: `ralph/**/*.py` (E501, FBT001, FBT002, TRY003, TRY300); `ralph/agents/__init__.py` (N802, PLC0415); `ralph/agents/_agy_upstream_diagnostic.py` (PLC0415); `ralph/agents/builtin_spec.py` (PLC0415, PLR0911, PLR0912); `ralph/agents/catalog.py` (PLC0415); `ralph/agents/completion_signals.py` (PLC0415); `ralph/agents/delegation_capabilities.py` (PLR0911, PLR2004); `ralph/agents/execution_state/_helpers.py` (PLC0415); `ralph/agents/invoke/__init__.py` (PLC0415); `ralph/agents/invoke/_pty_line_reader.py` (PLR0915); `ralph/agents/invoke/_runtime_resolvers/__init__.py` (ANN204); `ralph/agents/invoke/_workspace.py` (PLC0415); `ralph/agents/invoke/watchdog_observer.py` (PLC0415); `ralph/agents/multimodal_status.py` (PLR0911, PLR2004); `ralph/agents/parsers/__init__.py` (PLC0415); `ralph/agents/parsers/line_channel.py` (PLC0415); `ralph/agents/support.py` (PLC0415, PLR0911); `ralph/agents/vision_agent_provisioning.py` (PLR0911); `ralph/cli/**/*.py` (PLC0415); `ralph/config/**/*.py` (PLC0415); `ralph/config/__init__.py` (N802); `ralph/config/_env.py` (PLC0414); `ralph/diagnostics/**/*.py` (PLC0415); `ralph/display/**/*.py` (PLC0415); `ralph/display/__init__.py` (PLR0911); `ralph/display/parallel_display.py` (PLR2004); `ralph/display/record_writer.py` (PLR0911); `ralph/git/_lock_liveness.py` (PLR0911); `ralph/git/_lock_recovery.py` (S603); `ralph/git/git_command_queue.py` (PLC0415, PLR0911, PLR0912); `ralph/git/rebase/rebase_continuation.py` (PLC0415); `ralph/git/subprocess_runner.py` (PLC0415); `ralph/interrupt/controller.py` (PERF401); `ralph/interrupt/dispatcher.py` (PLC0415); `ralph/mcp/artifacts/canonical_submit.py` (PLC0415); `ralph/mcp/artifacts/commit_message.py` (PLC0415); `ralph/mcp/artifacts/commit_message_normalizer.py` (PLC0415); `ralph/mcp/artifacts/product_spec.py` (PLC0415); `ralph/mcp/artifacts/smoke_test_result.py` (PLC0415); `ralph/mcp/explore/**/*.py` (PLC0415, PLR0911, PLR0912, PLR0915, RUF012, TC003); `ralph/mcp/explore/_bench_r6_metrics.py` (S603); `ralph/mcp/explore/_bench_r6_subprocess.py` (S603); `ralph/mcp/explore/_store_class_file_rows.py` (S608); `ralph/mcp/multimodal/capabilities.py` (PLR0911); `ralph/mcp/protocol/_startup_timeouts.py` (PLC0415); `ralph/mcp/protocol/capability_mapping.py` (PLC0415); `ralph/mcp/protocol/startup.py` (PLC0415); `ralph/mcp/server/__init__.py` (PLC0415, PLR0911, PLR0912, PLR0915); `ralph/mcp/server/_fallback_standalone_server.py` (PLR2004); `ralph/mcp/server/_host_contention_signal.py` (TC003); `ralph/mcp/server/_http_tools_list.py` (S310); `ralph/mcp/server/_in_memory_transport.py` (N802); `ralph/mcp/server/_process_secrecy.py` (PLC0415); `ralph/mcp/server/_snapshots.py` (PLC0415); `ralph/mcp/server/lifecycle.py` (S310); `ralph/mcp/tool_contract.py` (PLC0415); `ralph/mcp/tools/_exec_jobs.py` (PLC0415); `ralph/mcp/tools/_validation_retry_hints.py` (PLC0415); `ralph/mcp/tools/bridge/_lazy_tool_handler.py` (PLC0415); `ralph/mcp/tools/bridge/_upstream_proxy_handler.py` (PLC0415); `ralph/mcp/tools/coordination.py` (PLC0415, S105); `ralph/mcp/tools/md_artifact.py` (PLC0415); `ralph/mcp/tools/workspace/**/*.py` (PLC0415, PLR0911, PLR0912, PLR0915, TC003); `ralph/mcp/transport/common.py` (PLC0415); `ralph/mcp/upstream/client.py` (PLC0415); `ralph/mcp/upstream/tool_catalog_cache.py` (PLC0415); `ralph/mcp/upstream/validation.py` (PLC0415); `ralph/mcp/websearch/backends/brave.py` (PLC0415); `ralph/mcp/websearch/backends/ddgs.py` (PLC0415); `ralph/mcp/websearch/backends/exa.py` (PLC0415); `ralph/mcp/websearch/backends/tavily.py` (PLC0415); `ralph/onboarding.py` (PLC0415); `ralph/phases/**/*.py` (PLC0415); `ralph/pipeline/**/*.py` (PLC0415); `ralph/pipeline/__init__.py` (N802); `ralph/pipeline/conflict_resolution/rebase_loop.py` (PLR0912); `ralph/pipeline/effect_executor.py` (PLR0915); `ralph/pipeline/factory.py` (PLR0912, PLR0915); `ralph/pipeline/plumbing/smoke_evidence.py` (S105); `ralph/pipeline/plumbing/smoke_multimodal.py` (PLC0415, PLR0911, PLR0912); `ralph/pipeline/plumbing/smoke_plumbing.py` (PLC0415, PLR0911, PLR0912, PLR0915); `ralph/pipeline/reducer.py` (PLR0911); `ralph/policy/__init__.py` (N802, PLC0415); `ralph/policy/_policy_validation.py` (PLC0415); `ralph/policy/loader.py` (PLC0415); `ralph/policy/validation/_api.py` (PLC0415); `ralph/policy/validation/_checkpoint_policy_mismatch_error.py` (PLC0415); `ralph/process/_spawn_env.py` (S105); `ralph/process/_spawn_validation.py` (PLC0415); `ralph/process/manager/_process_manager.py` (PLC0415); `ralph/process/manager/_supervisor.py` (S606); `ralph/process/pty.py` (S606); `ralph/process/teardown.py` (PLC0415); `ralph/project_policy/markers.py` (S105); `ralph/project_policy/remediation.py` (PLC0415); `ralph/prompts/__init__.py` (PLC0415); `ralph/prompts/materialize.py` (PLC0415); `ralph/recovery/__init__.py` (N802, PLC0415); `ralph/recovery/canary_probe.py` (PLC0415); `ralph/recovery/controller.py` (PLC0415); `ralph/recovery/failure_classifier.py` (PLC0415); `ralph/skills/__init__.py` (PLC0415); `ralph/supervising.py` (PLC0415); `ralph/testing/_audit_baseline.py` (S603); `ralph/testing/_filesystem_write_scan.py` (PLC0415); `ralph/testing/_single_owner_rules.py` (PLR0911); `ralph/testing/audit_comments.py` (S603); `ralph/visual/**/*.py` (PLR0911, PLR0912, PLR2004); `ralph/visual/design_verdict.py` (S105); `ralph/workspace/context.py` (PLC0415); `tests/**/*.py` (ANN001, ANN201, ANN202, ANN204, E501, FBT001, FBT002, FBT003, N802, PERF401, PLC0415, PLR0124, PLR2004, RUF043, S101, S105, S106, S108, S110, S112, S311, S603, S607, TC002, TC003, TRY003); `tests/test_integration_obstruction_real_git.py` (PLR0911). Each entry;s reason is recorded in the per-file-ignores allowlist in `ralph/testing/audit_lint_bypass.py`.
RALPH-FACT: suppression_rationale_policy: inline lint suppressions are forbidden in tracked source. The underlying lint warning is fixed in code, or suppressed in the tool configuration (`pyproject.toml` `[tool.ruff.lint.per-file-ignores]`), never inline. `ralph/testing/audit_lint_bypass.py` reports inline ruff suppressions and configuration entries that are not the documented per-file inventory, and `ralph/testing/audit_comments.py` fails on every comment token in tracked Python, so an inline suppression cannot land there.
RALPH-FACT: ci_gate_integration: `make verify` runs the ruff step as the first verify step (`ralph/verify.py:_VERIFY_STEPS`, label "ruff check ralph/ tests/"). `.github/workflows/verify.yml` invokes `cd ralph-workflow && make verify` on every push and pull_request event on the GitHub primary repository and fails the build when ruff exits non-zero. `.woodpecker.yml` continues the same gate on the Codeberg mirror while it is maintained; the CLA and publish workflows remain specialized.
RALPH-FACT: maintenance_evidence: ruff (Astral, Apache-2.0, https://docs.astral.sh/ruff/) — actively maintained, latest stable >= 0.6, the single source of truth for Python linting and formatting in this project. Recheck on a major ruff version bump, Astral deprecating the project, or a Python release that drops compatibility with ruff's baseline. Same scrutiny applies to vulture (dead-code audit, MIT) — recheck on the next major release or a Python 3.13+ deprecation that affects it.
RALPH-FACT: first_party_languages: Python (primary), Ruby (a single Homebrew formula at ralph-workflow/Formula/ralph-workflow.rb checked by the fail-closed `make formula-check` gate), JavaScript (one tracked file: the Sphinx documentation asset at ralph-workflow/docs/sphinx/_static/ralph-docs.js, shipped verbatim into the built HTML site through html_js_files in ralph-workflow/docs/sphinx/conf.py). JSON and YAML are non-code and have no per-language linting.
RALPH-FACT: excluded_language_evidence: TypeScript / .ts and .tsx are absent (file extension scan returns zero); Go source is absent (no go.mod); Rust source is quarantined under docs/legacy-rust/ and is outside any build target; the tracked tree carries exactly one JavaScript file (the Sphinx documentation asset at ralph-workflow/docs/sphinx/_static/ralph-docs.js) and no ESLint configuration or package manifest, so no JavaScript linter is wired into any gate. Each exclusion names the deterministic detector signal that triggered the inapplicability record, mirroring the same evidence used by the typecheck inapplicability record.
RALPH-FACT: dead_code_detection_per_language: Python -> `make -C ralph-workflow dead-code` runs `uv run --with vulture python -m vulture --config pyproject.toml` against the ralph package and tests (vulture is in dev extras, MIT, https://github.com/jendrikseipp/vulture); Ruby -> `make -C ralph-workflow formula-check` is a Ruby syntax check (it runs `ruby -c Formula/ralph-workflow.rb`), NOT a dead-code audit; the only Ruby file is the Homebrew formula at `ralph-workflow/Formula/ralph-workflow.rb`, treated as a single build-distribution artefact. Ruby is a declared prerequisite: absence fails non-zero with the gate-policy citation rather than reporting a false pass. The single tracked JavaScript documentation asset has no dead-code audit - no JavaScript dead-code tool is wired into any gate. Other detected languages have no dead-code audit wired because they carry no first-party source. All audit findings are review-required: a finding is a defect to repair, not a threshold to raise.
RALPH-FACT: retained_compatibility_inventory: zero retained first-party compatibility paths in the Python source. The legacy Rust sources under docs/legacy-rust/ are quarantined and excluded from every build and lint target. The Python wrapper `scripts/verify_social_proof.py` re-exports the `main` function of `scripts/fabrication_guard.py` as the entry point invoked by the social-proof gate step in `ralph/verify.py`.
RALPH-FACT: live_entry_point_annotation_inventory: zero live-entry annotations in the Python source. The framework/plugin-registry / FFI-style entry points — the agent stream parsers under ralph-workflow/ralph/agents/parsers/ (Claude / OpenCode / Codex / Cursor / Pi), the invocation-engine subprocess / PTY stream readers under ralph-workflow/ralph/agents/invoke/, and the MCP JSON envelope parser at ralph-workflow/ralph/mcp/server/_fallback_http_handler.py — are reachable through the standard entry-point import graphs: the parsers and stream readers are imported eagerly by the `python -m ralph` CLI graph and the envelope parser by the `ralph-mcp` MCP server graph. The parser registry is kept in sync with the builtin catalog by `ralph/testing/audit_agent_registry_sync.py`.

## Guard inventory

| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |
|---|---|---|---|---|---|---|
| No lint bypass | Lint owner | no inline lint suppression lands in the tree, and every `per-file-ignores` entry matches the documented per-file configuration | audit_lint_bypass returns zero findings | audit_lint_bypass returns zero findings | deterministic: lint bypass audit (audit_lint_bypass) | when the rule is restated as a kernel-level constraint |
| Public / wrapper syntax-proxy | Lint owner | making a method public or adding a wrapper solely to satisfy a rule is a violation, not a fix | no private method is renamed to public solely to satisfy a private-call ratchet; no wrapper exists only to make a structural rule pass | no private method is renamed to public solely to satisfy a private-call ratchet | advisory: code review verifies visibility; review: when private-call ratchet is reviewed | when every private-call ratchet is removed from the policy |

## AI execution instructions

Shared execution rules (truthful evidence, run every declared gate and report actual outcome, do not weaken gates, update facts in the same workflow, honest failure reporting) live in `docs/ralph-workflow-policy/execution-kernel.md` (v6). This file adds only its domain outcomes, facts, triggers, and exceptions. When a rule in this file conflicts with the kernel, the kernel wins and this file is amended.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-LANG: Python
RALPH-COMMAND: make -C ralph-workflow lint

RALPH-LANG: TypeScript
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no TypeScript source exists in this project (no `tsconfig.json`, no `.ts` files); reopens when the first `.ts` file lands - at which point `RALPH-COMMAND: npx eslint .` or `RALPH-COMMAND: npx tsc --noEmit` becomes the gate.; evidence: ralph-workflow/ralph/language_detector/ returns zero `.ts` / `.tsx` files; ESLint and tsc are not installed; no tsconfig.json; owner: project-policy owner; expiry: 2027-07-12; warning: TypeScript linting is genuinely inapplicable until the first .ts file lands; review trigger: when a `.ts` file is added under the workspace root.

RALPH-LANG: Rust
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no Rust source is built in this project (docs/legacy-rust/ is the quarantined pointer to the retired Rust implementation and is outside any lint target); reopens when active Rust code is reintroduced - at which point `RALPH-COMMAND: cargo clippy --all-targets -- -D warnings` becomes the gate.; evidence: no Cargo.toml at repo root; docs/legacy-rust/ is marked retired in its README and excluded from any lint target; clippy is not installed; owner: project-policy owner; expiry: 2027-07-12; warning: Rust linting is genuinely inapplicable while the legacy pointer stays retired; review trigger: when a new Cargo.toml lands or a Rust module is reintroduced outside docs/legacy-rust/.

RALPH-LANG: Go
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - no Go source exists in this project (no `go.mod`); reopens when the first Go file lands - at which point `RALPH-COMMAND: golangci-lint run ./...` becomes the gate.; evidence: no go.mod / go.sum at repo root; golangci-lint is not installed; ralph-workflow/ralph/language_detector/ scan reports zero `.go` files; owner: project-policy owner; expiry: 2027-07-12; warning: Go linting is genuinely inapplicable until a Go file lands; review trigger: when a go.mod lands at the workspace root.

RALPH-LANG: Ruby
RALPH-COMMAND: make -C ralph-workflow formula-check

RALPH-LANG: JavaScript
RALPH-INAPPLICABLE: exceptional case: no suitable maintained linter exists; reason - the only tracked JavaScript in this project is a Sphinx documentation asset (ralph-workflow/docs/sphinx/_static/ralph-docs.js, wired through html_js_files in ralph-workflow/docs/sphinx/conf.py) that ships verbatim into the built HTML site; there is no ESLint configuration, no package manifest, and no JavaScript linter wired into any gate, so no JavaScript lint step exists to run; reopens when a JavaScript source tree or a package manifest lands - at which point `RALPH-COMMAND: npx eslint .` becomes the gate.; evidence: git ls-files reports exactly one JavaScript file, the Sphinx asset; no package.json and no ESLint configuration exist in the tracked tree; the verification workflow (.github/workflows/verify.yml, .woodpecker.yml) installs Python, uv, and Ruby and runs no npm install step, so it wires no JavaScript linter; the language detector's 2000-file scan cap is reached before it reaches that asset, so the detected .js count comes from an ignored scratch file rather than from the tracked asset; owner: project-policy owner; expiry: 2027-07-12; warning: JavaScript linting is genuinely inapplicable while the documentation asset stays the only tracked JavaScript in the tree; review trigger: when a package.json or an ESLint configuration lands at the workspace root, or a JavaScript file is added outside ralph-workflow/docs/sphinx/_static/.

The expected successful result is a clean lint run (exit 0). Lint
failures MUST be addressed at the source, not by adding suppressions.
For the Ruby formula, a clean `ruby -c` syntax check (the only Ruby
gate the project needs). The target fails closed when Ruby is absent:
`scripts/packaging_operations.sh` returns 127 with the gate-policy
citation and `make formula-check` fails, so a machine without Ruby
never reports a false pass. The project-reality record is the
`formula_check_gate` fact in
`docs/ralph-workflow-policy/gate-script-policy.md`.

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
  review date: 2026-08-09

* publisher: ESLint Project
  title: "Configuring ESLint"
  http: https://eslint.org/docs/latest/use/configure/
  review date: 2026-08-09

* publisher: The Rust Project
  title: "Clippy documentation"
  http: https://doc.rust-lang.org/clippy/
  review date: 2026-08-09

* publisher: Google Engineering Practices
  title: "The CL Author's Guide to Getting Through Code Review"
  http: https://google.github.io/eng-practices/review/developer/
  review date: 2026-08-09

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
* Schema version: `<!-- ralph-policy-schema: v6 -->`
