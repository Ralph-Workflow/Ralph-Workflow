# Required Verification (before PR/completion)

## Canonical command

```bash
cargo xtask verify
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics**. Informational output is acceptable.

If verification exposes a pre-existing failure, or if you discover any other pre-existing repo issue while working, it becomes fix-now work immediately. Do not defer it, work around it, or leave it for another contributor.

### Parallel execution architecture

`cargo xtask verify` runs a shared serial preparation step, a serial native-check gate, and then seven concurrent lanes:

- **Phase 0 (serial, warm-run optimization):** Prepare verify cache state once for the full check set. `xtask` precomputes unique scope hashes, native required-check hashes, and the native-scan eligibility hash before any verify check starts, so later cache lookups are O(1) map reads instead of repeated scope traversal in each lane or native-check dispatch.
- **Phase 1 (serial):** Native checks — Rust function checks (compliance-timeout-wrapper, audit-no-shell-scripts). On unchanged warm runs, `xtask` may satisfy these from the verify cache instead of rescanning the same inputs.
- **Phase 2 (concurrent):**
  - Lane 1: Native Aho-Corasick scan (pure file I/O, no target/ interaction)
  - Lane 2: `cargo fmt --all --check` (no target/ interaction, zero contention)
  - Lane 3: Core cargo (clippy-core, test-ralph-workflow-lib, test-integration — default target/)
  - Lane 4: Xtask cargo (clippy-xtask, test-xtask — target/xtask-parallel-verify)
  - Lane 5: GUI cargo (clippy-ralph-gui, test-ralph-gui-lib — target/gui-parallel-verify)
  - Lane 6: Frontend (bun install, lint, test — independent of cargo)
  - Lane 7: Release (release build, dylint — target/release-parallel-verify)

Result priority: scan > fmt > core_cargo > xtask > gui > frontend > release.

On an unchanged tree, `cargo xtask verify` may reuse cached clean results for eligible lanes, including the serial native checks and the native scan lane. This does not weaken the contract: each cache key includes the relevant source inputs plus the `xtask` implementation inputs for that verifier, so any relevant source or verifier change invalidates the warm-run shortcut and the check runs again.
Warm runs also persist per-file content fingerprints in `target/xtask-verify-cache.json`, allowing a fresh `cargo xtask verify` process to reuse unchanged digests instead of rereading every scoped file byte before deciding on cache hits. There is no fixed cross-machine runtime target, but unchanged warm runs should spend noticeably less time in cache-eligibility work than cold runs because the preparation step shares those hashes across all lanes up front.

The release lane is intentionally narrower than the full workspace: `release-build` runs `cargo build --release` against workspace default members, so warm-cache reuse should survive edits under `tests/` while still invalidating on changes to `ralph-workflow`, `test-helpers`, `xtask`, manifests, or lockfiles.

---

## Optional Developer Tools

### Coverage tooling

For local coverage analysis (diagnostic, not a CI gate):

```bash
cargo install cargo-llvm-cov --locked
```

This is an optional developer setup step for inspecting code coverage locally. It is **not required** for CI verification and is not a required dependency.

After touching any module refactored under the fp-style-compliance plan, run:

```bash
cargo xtask coverage
```

Low coverage on a module is a signal to ask *"do we understand the failure modes here?"* — it is a prompt for investigation, not a gate to block PRs.

---

## Reference: underlying commands

Run git rebase on main if on feature branch.

```bash
# Check for forbidden allow/expect attributes (must return exit 1: no matches is success)
rg -n -U --pcre2 '(?m)^\s*#\s*!?\[\s*(?:(?:allow|expect)\s*\(|cfg_attr\s*\((?:[^()]|\([^()]*\))*?,\s*(?:allow|expect)\s*\()' --glob '!target/**' --glob '!.git/**' --glob '*.rs' .

# No test flags in production code — 6 rg checks targeting ralph-workflow/src/
rg -n 'cfg!\(test\)' ralph-workflow/src/ --glob '*.rs'
rg -n '(test_mode|is_test|is_testing|testing_mode)\s*:\s*bool' ralph-workflow/src/ --glob '*.rs'
rg -n '(skip_validation|skip_verify|skip_check|skip_auth|skip_api)\s*:\s*bool' ralph-workflow/src/ --glob '*.rs'
rg -n '(mock_mode|fake_mode|stub_mode|use_mock|use_fake|use_stub)\s*:\s*bool' ralph-workflow/src/ --glob '*.rs'
rg -n '#\[cfg\(feature\s*=\s*"testing"\)\]' ralph-workflow/src/ --glob '*.rs'
rg -n '#\[cfg\(not\(test\)\)\]' ralph-workflow/src/ --glob '*.rs'
# All must return exit 1 (no matches) to pass.

# Integration test compliance — process-spawn and serial checks
rg -n 'std::process::Command::new|assert_cmd::Command::new' tests/integration_tests/ --glob '*.rs' --glob '!_TEMPLATE.rs'
rg -n '#\[serial\]|use serial_test' tests/integration_tests/ --glob '*.rs' --glob '!_TEMPLATE.rs'
# Both must return exit 1 (no matches) to pass.
# Timeout-wrapper compliance is enforced natively by `cargo xtask verify`
# (compliance-timeout-wrapper native check in xtask/src/compliance.rs).

# Audit tests for design compliance — 9 rg checks
rg -n 'cfg!\(test\)' tests/integration_tests/ --glob '*.rs'
rg -n 'std::fs::|TempDir|tempfile::' tests/integration_tests/ --glob '*.rs'
rg -n 'std::process::Command::new' tests/integration_tests/ --glob '*.rs'
rg -n '#\[serial\]' ralph-workflow/src/ --glob '*.rs'
rg -n 'use test_helpers::|init_git_repo|commit_all|git_switch' ralph-workflow/src/ --glob '*.rs'
rg -n 'std::env::set_var|std::env::remove_var|env::set_var|env::remove_var' tests/integration_tests/ --glob '*.rs'
rg -n '#\[serial\]' tests/process_system_tests/ --glob '*.rs'
rg -n 'git2::|init_git_repo' tests/process_system_tests/ --glob '*.rs'
rg --pcre2 -n '#\[ignore\b(?!.*https://)' tests/ ralph-workflow/src/ --glob '*.rs'
# All must return exit 1 (no matches) to pass.

# Format check
cargo fmt --all --check

# Lint core crates (ralph-workflow + ralph-workflow-tests + test-helpers) in a single invocation
# Note: Enforces clippy::all plus explicit deny rules (unwrap_used, panic, indexing_slicing, etc.)
# via #![deny(...)] and #![forbid(unsafe_code)] attributes in lib.rs and main.rs
# (clippy::cargo is not enabled as it flags ecosystem-level dependency conflicts)
cargo clippy -p ralph-workflow -p ralph-workflow-tests -p test-helpers --all-targets --all-features -- -D warnings

# Lint xtask runner (runs in parallel group with separate target dir)
cargo clippy -p xtask --all-targets -- -D warnings

# Lint ralph-gui (runs in parallel group with separate target dir)
cargo clippy -p ralph-gui --all-targets -- -D warnings

# Frontend install
bun install --cwd ralph-gui/ui --frozen-lockfile

# Frontend checks
bun --cwd ralph-gui/ui run lint
bun --cwd ralph-gui/ui run test

# Unit tests
cargo test -p xtask
cargo test -p ralph-gui --lib
cargo test -p ralph-workflow --lib --all-features

# Drain/chain architecture changes (named chains, drain bindings, checkpoint drain metadata)
cargo test -p ralph-workflow agents::config::file::tests
cargo test -p ralph-workflow agents::registry::tests
cargo test -p ralph-workflow agents::validation::tests
cargo test -p ralph-workflow-tests --test integration_tests agent_chain_normalization

# Default config template / registry wiring regressions
# Keep the built-in `ralph-workflow/examples/agents.toml` template on the named chain + drain schema
# and ensure `AgentRegistry::new()` consumes the same resolved drain bindings.

# Integration tests
cargo test -p ralph-workflow-tests --test integration_tests

# Process system tests (parallel, manual only — not in CI)
cargo test -p ralph-workflow-tests --test process-system-tests

# Timeout / child-process relevance changes
# Run this focused process-topology check when changing idle-timeout suppression,
# descendant relevance, or child-process observability logic.
cargo test -p ralph-workflow-tests --test process-system-tests child_process_timeout_detection

# Memory safety verification (bounded growth, thread cleanup, Arc patterns)
cargo test -p ralph-workflow-tests --test integration_tests memory_safety
cargo test -p ralph-workflow --lib benchmarks
cargo test -p ralph-workflow --lib executor::tests

# Per-run logging tests (when changing logging infrastructure)
cargo test -p ralph-workflow-tests --test integration_tests logging_per_run

# Metrics regressions (when changing iteration/retry/continuation/fallback logic)
cargo test -p ralph-workflow --lib reducer::state_reduction::tests::metrics
cargo test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo test -p ralph-workflow-tests --test integration_tests summary_consistency

# Release build
cargo build --release

# Custom lints (dylint) - all lints consolidated in ralph_lints
#
# IMPORTANT:
# - The xtask dylint runner lints every workspace package except lint crates (e.g. *_lints).
# - It keeps `ralph-workflow` on `--lib` only to avoid known binary-target warning escalation
#   (`#![deny(warnings)]` in `ralph` binary).
# - The Makefile automatically ensures nightly toolchain's cargo is used for driver builds,
#   even when system cargo (Homebrew/apt) is stable.
#
# Recommended:
make dylint
# or:
cargo xtask dylint
```

**If any command fails or emits ERROR/WARNING diagnostics, FIX IT before continuing.** No ignored tests allowed.

**Broader policy:** known pre-existing issues anywhere in the repo must also be fixed immediately, even when they are outside the narrow change you started with.

For dylint details/troubleshooting, see `docs/tooling/dylint.md`.
