# Required Verification (before PR/completion)

## Canonical command

```bash
cargo xtask verify
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics**. Informational output is acceptable.

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

# Lint main crate (all targets: lib, tests, benchmarks, examples)
# Note: Enforces clippy::all, clippy::pedantic, clippy::nursery
# via #![deny(...)] attributes in lib.rs and main.rs
# (clippy::cargo is not enabled as it flags ecosystem-level dependency conflicts)
cargo clippy -p ralph-workflow --all-targets --all-features -- -D warnings

# Lint integration tests
# Note: Enforces clippy::all, clippy::pedantic, clippy::nursery
# via #![deny(...)] attributes in tests/integration_tests/main.rs and tests/system_tests/main.rs
# (clippy::cargo is not enabled as it flags ecosystem-level dependency conflicts)
cargo clippy -p ralph-workflow-tests --all-targets -- -D warnings

# Lint test helpers
# Enforces clippy::all, clippy::pedantic, clippy::nursery
# via #![deny(...)] attributes in test-helpers/src/lib.rs
cargo clippy -p test-helpers --all-targets -- -D warnings

# Lint xtask runner
cargo clippy -p xtask --all-targets -- -D warnings

# Unit tests
cargo test -p xtask
cargo test -p ralph-workflow --lib --all-features

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
cargo test -p ralph-workflow-tests logging_per_run

# Release build
cargo build --release

# Custom lints (dylint) - check for files exceeding line limits
# This runs the file_too_long lint from lints/file_too_long
#
# IMPORTANT:
# - Running dylint against the `ralph` binary target can fail the build because the binary uses
#   `#![deny(warnings)]` (warnings become hard errors).
# - Run the lint against the `ralph-workflow` *library* target instead.
# - The Makefile automatically ensures nightly toolchain's cargo is used for driver builds,
#   even when system cargo (Homebrew/apt) is stable.
#
# Recommended (library target only):
make dylint
# or:
cargo dylint -p ralph-workflow --lib file_too_long -- --lib
```

**If any command fails or emits ERROR/WARNING diagnostics, FIX IT before continuing.** No ignored tests allowed.

For dylint details/troubleshooting, see `docs/tooling/dylint.md`.
