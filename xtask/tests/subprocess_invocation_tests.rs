// Subprocess invocation tests for xtask CLI flags.
//
// RECURSION PROTECTION: These tests spawn `cargo xtask` subprocesses.
// They MUST NOT run as part of the standard verify pipeline (cargo test -p xtask).
// They live here (tests/) rather than src/ so they form a separate binary
// that verify does NOT include in test-xtask.
//
// If RALPH_XTASK_IN_VERIFY is set, skip all tests to prevent recursion.

fn skip_if_in_verify() {
    if std::env::var("RALPH_XTASK_IN_VERIFY").is_ok() {
        eprintln!("skipping subprocess tests: RALPH_XTASK_IN_VERIFY is set");
        std::process::exit(0);
    }
}

#[test]
fn test_dylint_help_flag_exits_successfully() {
    skip_if_in_verify();
    // When run with --help or -h, should exit with code 0
    // This test verifies the help text parsing works (actual help text is printed to stderr)
    let result = std::process::Command::new("cargo")
        .args(["xtask", "dylint", "--help"])
        .output()
        .expect("cargo xtask dylint --help should execute");

    // Help should succeed (exit code 0)
    assert!(
        result.status.success(),
        "dylint --help should exit successfully"
    );
}

#[test]
fn test_dylint_verbose_flag_is_accepted() {
    skip_if_in_verify();
    // When run with --verbose or -v, should not fail on argument parsing
    // This test verifies the verbose flag is recognized (actual execution may fail due to env)
    let result = std::process::Command::new("cargo")
        .args(["xtask", "dylint", "--verbose"])
        .output()
        .expect("cargo xtask dylint --verbose should execute");

    // Should not fail on argument parsing - the error should be about env setup, not args
    let stderr = String::from_utf8_lossy(&result.stderr);
    // If it fails, it should fail with a meaningful error about environment, not unknown flag
    if !result.status.success() {
        assert!(
            !stderr.contains("unknown option"),
            "verbose flag should be recognized, got: {}",
            stderr
        );
    }
}

#[test]
fn test_verify_help_flag_exits_successfully() {
    skip_if_in_verify();
    // When run with --help or -h, should exit with code 0
    let result = std::process::Command::new("cargo")
        .args(["xtask", "verify", "--help"])
        .output()
        .expect("cargo xtask verify --help should execute");

    // Help should succeed (exit code 0)
    assert!(
        result.status.success(),
        "verify --help should exit successfully"
    );
}

#[test]
fn test_lsp_forbidden_allow_expect_help_flag_exits_successfully() {
    skip_if_in_verify();
    let result = std::process::Command::new("cargo")
        .args(["xtask", "lsp-forbidden-allow-expect", "--help"])
        .output()
        .expect("cargo xtask lsp-forbidden-allow-expect --help should execute");

    assert!(
        result.status.success(),
        "lsp-forbidden-allow-expect --help should exit successfully"
    );
}
