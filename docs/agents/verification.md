# Required Verification (before PR/completion)

## Canonical command

```bash
# Remote-first by default (recommended — no flags needed):
cargo xtask verify

# EMERGENCY ONLY — use ONLY when rw-build-server is confirmed unreachable
# (network down, server offline). NEVER use to work around a test failure
# or for convenience — that defeats the entire remote-first design.
XTASK_LOCAL=1 cargo xtask verify
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics**. Informational output is acceptable.

`cargo xtask` is remote-first: on startup it checks whether the current working directory is under `/tmp` (already on a build server) or `XTASK_LOCAL=1` is set; if neither, it probes `rw-build-server` via SSH with a 5-second timeout, rsyncs the working tree (`--exclude=.git/` plus `.gitignore`-based filtering), initializes a minimal git repo on the remote (for libgit2/git-dependent tests), then re-executes the same subcommand on the remote and streams output to the local terminal. If the remote is unreachable, it prints a single warning and falls back to running locally. Exit codes are propagated correctly in both paths. This applies to **all** `cargo xtask` subcommands (verify, dylint, coverage, etc.).

**`XTASK_LOCAL=1` is an emergency-only override** for when the remote is confirmed down (internet or SSH failure). Setting it for convenience or to work around test failures defeats the entire remote-first design and is never acceptable.

For non-xtask commands (`cargo test`, `cargo build`, etc.), use `./scripts/remote/run.sh` — see `docs/tooling/remote-build.md`.

**Note:** The remote machine runs Debian Linux (x86_64). `cargo xtask verify` produces no binary artifacts that need copy-back, so the macOS/Linux difference is transparent for the verification use case.

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
  - Lane 5: Release (dylint — target/release-parallel-verify)

Result priority: scan > fmt > core_cargo > xtask > release.

On an unchanged tree, `cargo xtask verify` may reuse cached clean results for eligible lanes, including the serial native checks and the native scan lane. This does not weaken the contract: each cache key includes the relevant source inputs plus the `xtask` implementation inputs for that verifier, so any relevant source or verifier change invalidates the warm-run shortcut and the check runs again.
Warm runs also persist per-file content fingerprints in `target/xtask-verify-cache.json`, allowing a fresh `cargo xtask verify` process to reuse unchanged digests instead of rereading every scoped file byte before deciding on cache hits. There is no fixed cross-machine runtime target, but unchanged warm runs should spend noticeably less time in cache-eligibility work than cold runs because the preparation step shares those hashes across all lanes up front.

The release lane is intentionally narrower than the full workspace: `release-build` runs `cargo build --profile release-verify` (thin LTO) against workspace default members, so warm-cache reuse should survive edits under `tests/` while still invalidating on changes to `ralph-workflow`, `test-helpers`, `xtask`, manifests, or lockfiles. The `release-verify` profile inherits from `release` but uses thin LTO instead of full LTO, significantly reducing verification build time while still catching the same link errors.

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

> **Do not run these commands directly.** Use `cargo xtask verify` (which runs them all on the remote build server) or `cargo xtask <cmd>` for individual commands. Direct `cargo test` / `cargo clippy` / `cargo build` runs locally and overheats your laptop. See `docs/tooling/remote-build.md`.

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
cargo xtask fmt --all --check

# Lint core crates (ralph-workflow + ralph-workflow-tests + test-helpers) in a single invocation
# Note: Enforces clippy::all plus explicit deny rules (unwrap_used, panic, indexing_slicing, etc.)
# via #![deny(...)] and #![forbid(unsafe_code)] attributes in lib.rs and main.rs
# (clippy::cargo is not enabled as it flags ecosystem-level dependency conflicts)
cargo xtask clippy -p ralph-workflow -p ralph-workflow-tests -p test-helpers --all-targets --all-features -- -D warnings

# Lint xtask runner (runs in parallel group with separate target dir)
cargo xtask clippy -p xtask --all-targets -- -D warnings

# Unit tests
cargo xtask test -p xtask
cargo xtask test -p ralph-workflow --lib --all-features

# Drain/chain architecture changes (named chains, drain bindings, checkpoint drain metadata)
cargo xtask test -p ralph-workflow agents::config::file::tests
cargo xtask test -p ralph-workflow agents::registry::tests
cargo xtask test -p ralph-workflow agents::validation::tests
cargo xtask test -p ralph-workflow-tests --test integration_tests agent_chain_normalization

# Default config template / registry wiring regressions
# Keep the built-in `ralph-workflow/examples/agents.toml` template on the named chain + drain schema
# and ensure `AgentRegistry::new()` consumes the same resolved drain bindings.

# Integration tests
cargo xtask test -p ralph-workflow-tests --test integration_tests

# Process system tests (parallel, manual only — not in CI)
cargo xtask test -p ralph-workflow-tests --test process-system-tests

# Timeout / child-process relevance changes
# Run this focused process-topology check when changing idle-timeout suppression,
# descendant relevance, or child-process observability logic.
cargo xtask test -p ralph-workflow-tests --test process-system-tests child_process_timeout_detection

# Memory safety verification (bounded growth, thread cleanup, Arc patterns)
cargo xtask test -p ralph-workflow-tests --test integration_tests memory_safety
cargo xtask test -p ralph-workflow --lib benchmarks
cargo xtask test -p ralph-workflow --lib executor::tests

# Per-run logging tests (when changing logging infrastructure)
cargo xtask test -p ralph-workflow-tests --test integration_tests logging_per_run

# Metrics regressions (when changing iteration/retry/continuation/fallback logic)
cargo xtask test -p ralph-workflow --lib reducer::state_reduction::tests::metrics
cargo xtask test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo xtask test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo xtask test -p ralph-workflow-tests --test integration_tests summary_consistency

# Release build (runs as part of cargo xtask verify --gui)
# Uses --profile release-verify (thin LTO) for faster verification
cargo xtask build --profile release-verify

# Custom lints (dylint) - all lints consolidated in ralph_lints
#
# IMPORTANT:
# - The xtask dylint runner lints every workspace package except lint crates (e.g. *_lints).
# - It keeps `ralph-workflow` on `--lib` only to avoid known binary-target warning escalation
#   (`#![deny(warnings)]` in `ralph` binary).
#
# Recommended:
cargo xtask dylint
```

**If any command fails or emits ERROR/WARNING diagnostics, FIX IT before continuing.** No ignored tests allowed.

**Broader policy:** known pre-existing issues anywhere in the repo must also be fixed immediately, even when they are outside the narrow change you started with.

For dylint details/troubleshooting, see `docs/tooling/dylint.md`.
