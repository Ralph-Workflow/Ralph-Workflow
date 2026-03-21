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

        let dylint_body = {
            let start = makefile
                .find("\ndylint:")
                .expect("Makefile should contain a dylint: target")
                + 1;
            let rest = &makefile[start..];
            let end = rest.find("\ndylint-verbose:").unwrap_or(rest.len());
            &rest[..end]
        };

        assert!(
            dylint_body.contains("$(CARGO) xtask dylint"),
            "dylint target should delegate to cargo xtask dylint"
        );

        let verbose_body = {
            let start = makefile
                .find("\ndylint-verbose:")
                .expect("Makefile should contain a dylint-verbose target")
                + 1;
            let rest = &makefile[start..];
            let end = rest.find("\n\n").unwrap_or(rest.len());
            &rest[..end]
        };

        assert!(
            verbose_body.contains("rustup which cargo --toolchain \"$$NIGHTLY_TOOLCHAIN\""),
            "dylint-verbose should resolve nightly cargo via rustup"
        );
        assert!(
            verbose_body.contains("export PATH=\"$$WRAPPER_DIR:$$NIGHTLY_BIN_DIR:$$PATH\""),
            "dylint-verbose should prepend wrapper + nightly bin dir to PATH"
        );
        assert!(
            verbose_body.contains("export RUSTUP_TOOLCHAIN=\"$$NIGHTLY_TOOLCHAIN\""),
            "dylint-verbose should export RUSTUP_TOOLCHAIN"
        );
        assert!(
            !verbose_body.contains(
                "rustup component add rustc-dev llvm-tools-preview --toolchain \"$$NIGHTLY_TOOLCHAIN\" || true"
            ),
            "dylint-verbose must not suppress rustup component install failures"
        );
        assert!(
            verbose_body.contains("if ! rustup toolchain list | grep -qE \"^nightly\"; then"),
            "dylint-verbose should install nightly only when missing"
        );
        assert!(
            verbose_body.contains("HOME_DIR=\"$${HOME:-}\""),
            "dylint-verbose should guard access to HOME under bash -u"
        );
    });
}

#[test]
fn make_dylint_targets_check_cargo_home_before_registry_subdirs() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        let target = "dylint-verbose";
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
    });
}

#[test]
fn make_dylint_targets_do_not_force_offline_mode_from_partial_registry_cache() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        let target = "dylint-verbose";
        let start = makefile
            .find(&format!("\n{target}:"))
            .expect("Makefile should contain dylint target")
            + 1;
        let rest = &makefile[start..];
        let end = rest.find("\n\n").unwrap_or(rest.len());
        let body = &rest[..end];

        assert!(
            !body.contains(
                "if [ -z \"$${CARGO_NET_OFFLINE:-}\" ] && [ -e \"$$CARGO_HOME/registry/cache\" ] && [ -e \"$$CARGO_HOME/registry/index\" ]; then"
            ),
            "{target} should not force offline mode merely because registry cache/index directories exist"
        );
        assert!(
            body.contains("if [ \"$${DYLINT_FORCE_OFFLINE:-0}\" = \"1\" ]; then")
                && body.contains("export CARGO_NET_OFFLINE=true;"),
            "{target} should keep offline mode opt-in via DYLINT_FORCE_OFFLINE"
        );
    });
}
