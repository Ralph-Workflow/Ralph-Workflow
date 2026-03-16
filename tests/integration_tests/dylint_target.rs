//! Integration test for the `make dylint` target.
//!
//! This validates that the Makefile's dylint targets delegate to cargo xtask dylint,
//! rather than containing complex inline bash logic. This keeps the Makefile simple
//! and maintains separation of concerns.
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

/// Extract a Makefile target body by finding the target and extracting until the next target (next line starting with word characters followed by colon)
fn extract_makefile_target(makefile: &str, target_name: &str) -> String {
    let pattern = format!("\n{target_name}:");
    let start = match makefile.find(&pattern) {
        Some(pos) => pos + 1, // skip the newline
        None => return String::new(),
    };

    // Find the end: next line that's a target (word followed by colon) or double newline
    let rest = &makefile[start..];
    let mut end = rest.len();

    // Look for next target pattern (lines that look like targets: "target:" or "target: ")
    for (i, line) in rest.lines().enumerate() {
        if i == 0 {
            continue; // skip the target line itself
        }
        // A target line is one that contains a colon but not in a variable reference
        // Simple heuristic: starts with optional whitespace, then word characters, then colon
        let trimmed = line.trim();
        if !trimmed.is_empty()
            && trimmed.contains(':')
            && !trimmed.contains("$$")
            && !trimmed.starts_with('\t')
            && !trimmed.starts_with(" if")
            && !trimmed.starts_with(" else")
            && !trimmed.starts_with(" fi")
        {
            // Check if this looks like a target (has colon at start or after word chars)
            if trimmed.starts_with(char::is_alphabetic) || trimmed.starts_with('_') {
                // This is likely a new target
                end = start + rest[..i].rfind('\n').map(|p| p + 1).unwrap_or(0);
                break;
            }
        }
        // Also stop at double newline (paragraph break)
        if line.is_empty() && rest.lines().nth(i + 1).is_some_and(|l| l.is_empty()) {
            end = start + rest[..i].rfind('\n').map(|p| p + 1).unwrap_or(0);
            break;
        }
    }

    makefile[start..start + end].to_string()
}

#[test]
fn make_dylint_target_delegates_to_xtask() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        // The dylint target should delegate to cargo xtask dylint
        assert!(
            makefile.contains("dylint:\n\t$(CARGO) xtask dylint"),
            "dylint target should delegate to 'cargo xtask dylint'"
        );
    });
}

#[test]
fn make_dylint_verbose_target_delegates_to_xtask_verbose() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        // The dylint-verbose target should delegate to cargo xtask dylint --verbose
        assert!(
            makefile.contains("dylint-verbose:\n\t$(CARGO) xtask dylint --verbose"),
            "dylint-verbose target should delegate to 'cargo xtask dylint --verbose'"
        );
    });
}

#[test]
fn make_dylint_targets_do_not_contain_complex_inline_bash() {
    with_default_timeout(|| {
        let makefile = include_str!("../../Makefile");

        // The dylint targets should NOT contain the complex bash patterns that were
        // previously inline in the Makefile. This ensures the Makefile is now
        // a thin wrapper delegating to xtask.

        // Extract dylint target body
        let dylint_body = extract_makefile_target(makefile, "dylint");

        // The target should be simple (just delegating to xtask)
        // It should NOT contain complex bash patterns
        assert!(
            !dylint_body.contains("rustup which cargo"),
            "dylint target should NOT contain inline rustup logic (should delegate to xtask)"
        );
        assert!(
            !dylint_body.contains("rustup toolchain install"),
            "dylint target should NOT contain inline toolchain install (should delegate to xtask)"
        );
        assert!(
            !dylint_body.contains("rustup component add"),
            "dylint target should NOT contain inline component install (should delegate to xtask)"
        );
        assert!(
            !dylint_body.contains("cargo install cargo-dylint"),
            "dylint target should NOT contain inline cargo-dylint install (should delegate to xtask)"
        );
        assert!(
            !dylint_body.contains("export PATH=\"$WRAPPER_DIR"),
            "dylint target should NOT contain wrapper logic (should delegate to xtask)"
        );
    });
}
