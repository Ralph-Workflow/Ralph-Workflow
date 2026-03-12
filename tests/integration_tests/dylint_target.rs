//! Integration test for the `make dylint` target.
//!
//! This validates the Makefile's behavior in the mixed-install scenario where:
//! - a stable `cargo` exists on PATH (e.g., Homebrew/apt)
//! - `rustup` provides the nightly toolchain
//!
//! The regression we care about is that cargo-dylint may spawn a subprocess that
//! unsets `RUSTUP_TOOLCHAIN` and then invokes plain `cargo`, which would resolve
//! to the stable cargo on PATH unless the Makefile prepends the nightly toolchain
//! bin directory (or otherwise forces resolution).
//!
//! Per integration test rules, we do not spawn external processes (no `make`,
//! no `cargo`, no `rustup`). We assert the observable, deterministic behavior
//! of the Makefile content itself.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;

#[test]
fn make_dylint_target_forces_nightly_cargo_resolution() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        // Scope assertions to the `dylint:` recipe body so that similar lines in
        // `dylint-verbose` do not mask regressions.
        let dylint_body = {
            let start = makefile
                .find("\ndylint:")
                .expect("Makefile should contain a dylint: target")
                + 1;
            let rest = &makefile[start..];
            let end = rest.find("\ndylint-verbose:").unwrap_or(rest.len());
            &rest[..end]
        };

        // Ensure we compute the nightly cargo path via rustup.
        assert!(
            dylint_body.contains("rustup which cargo --toolchain \"$$NIGHTLY_TOOLCHAIN\""),
            "dylint target should resolve nightly cargo via rustup with NIGHTLY_TOOLCHAIN variable"
        );

        // Ensure we prepend a PATH entry so that cargo-dylint's internal `cargo`
        // subprocesses resolve to nightly even if `RUSTUP_TOOLCHAIN` is unset.
        assert!(
            dylint_body.contains("export PATH=\"$$WRAPPER_DIR:$$NIGHTLY_BIN_DIR:$$PATH\""),
            "dylint target should prepend wrapper + nightly bin dir to PATH"
        );

        // Ensure we use a wrapper `cargo` script which re-exports RUSTUP_TOOLCHAIN
        // to mitigate cargo-dylint unsetting it for driver rebuilds.
        assert!(
            dylint_body.contains("export RUSTUP_TOOLCHAIN=\"$$NIGHTLY_TOOLCHAIN\""),
            "dylint target should export RUSTUP_TOOLCHAIN to nightly toolchain variable"
        );

        assert!(
            dylint_body.contains(
                "cargo dylint -q --path lints/file_too_long -p ralph-workflow -- --lib --quiet"
            ),
            "dylint target should lint the ralph-workflow library target only"
        );

        // We should not suppress rustup component installation failures.
        assert!(
            !dylint_body.contains(
                "rustup component add rustc-dev llvm-tools-preview --toolchain \"$$NIGHTLY_TOOLCHAIN\" || true"
            ),
            "dylint target must not suppress rustup component install failures"
        );

        // Offline/hermetic acceptance: do not unconditionally invoke a network-dependent
        // toolchain install when nightly is already installed.
        // (We allow toolchain install only when nightly is missing.)
        let has_guarded_nightly_install =
            dylint_body.contains("if ! rustup toolchain list | grep -qE \"^nightly\"; then");
        assert!(
            has_guarded_nightly_install,
            "dylint target should only install nightly when missing"
        );

        // Unset HOME should yield an actionable message before bash -u fails.
        let guarded_home_pattern = concat!("HOME_DIR=\"$$", "{HOME:-}\"");
        assert!(
            dylint_body.contains(guarded_home_pattern),
            "dylint target should guard access to HOME under bash -u"
        );
    });
}

#[test]
fn make_dylint_targets_check_cargo_home_before_registry_subdirs() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        for target in ["dylint", "dylint-verbose"] {
            let start = makefile
                .find(&format!("\n{target}:"))
                .expect("Makefile should contain dylint target")
                + 1;
            let rest = &makefile[start..];
            let end = rest.find("\n\n").unwrap_or(rest.len());
            let body = &rest[..end];

            let cargo_home_check = body
                .find("if ! mkdir -p \"$$CARGO_HOME\" 2>/dev/null; then")
                .expect("target should check CARGO_HOME access");
            let registry_mkdir = body
                .find("mkdir -p \"$$CARGO_HOME/registry\" \"$$CARGO_HOME/registry/src\" \"$$CARGO_HOME/bin\";")
                .expect("target should prepare cargo home subdirectories");

            assert!(
                cargo_home_check < registry_mkdir,
                "{target} should validate CARGO_HOME before creating registry/bin subdirectories"
            );
        }
    });
}
