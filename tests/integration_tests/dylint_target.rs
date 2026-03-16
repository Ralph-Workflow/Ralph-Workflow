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
        // A target line must:
        // 1. Start with alphanumeric or underscore (not a variable or directive)
        // 2. Contain a colon (the target separator)
        // 3. NOT be a variable assignment (VAR := value) or conditional (if/else/fi)
        let trimmed = line.trim();
        let is_target = trimmed.starts_with(char::is_alphabetic) || trimmed.starts_with('_');
        let has_colon = trimmed.contains(':');
        let is_variable_assignment = trimmed.contains(" :=")
            || trimmed.starts_with("if ")
            || trimmed.starts_with("else")
            || trimmed.starts_with("fi")
            || trimmed.starts_with(" endif");

        if is_target && has_colon && !is_variable_assignment {
            // This is likely a new target - calculate end position
            // Find the newline before this line
            if let Some(newline_pos) = rest[..i].rfind('\n') {
                end = newline_pos + 1;
                break;
            }
        }
        // Also stop at double newline (paragraph break)
        if line.is_empty() && rest.lines().nth(i + 1).is_some_and(|l| l.is_empty()) {
            if let Some(newline_pos) = rest[..i].rfind('\n') {
                end = newline_pos + 1;
                break;
            }
        }
    }

    // Ensure end is within bounds
    let actual_end = std::cmp::min(end, rest.len());
    makefile[start..start + actual_end].to_string()
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
