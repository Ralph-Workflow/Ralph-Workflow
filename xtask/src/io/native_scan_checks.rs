//! Native scan check definitions.
//!
//! All native scan checks are defined here as a const array, replacing 17 separate
//! `rg` subprocess calls.

use crate::io::native_scan_types::{MatchMode, NativeScanCheck};

const LINT_SCAN_EXCLUDE_GLOBS: &[&str] = &[
    "**/node_modules/**",
    "**/dist/**",
    "**/ui/**",
    "**/target/**",
    // verify.rs contains FORBIDDEN_ALLOW_EXPECT_POLICY which documents the lint policy
    // using literal examples of the forbidden patterns - these are not actual violations
    "verify.rs",
];

/// All native scan checks, replacing 17 `rg` subprocess calls.
///
/// Groups are inferred at runtime by identical `(sorted-directories, include_glob)` keys.
pub const NATIVE_SCAN_CHECKS: &[NativeScanCheck] = &[
    // ── ralph-workflow/src group ──────────────────────────────────────────────
    NativeScanCheck {
        name: "no-test-flags-cfg-test",
        literals: &["cfg!(test)"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "no-test-flags-test-mode-params",
        literals: &["test_mode", "is_test", "is_testing", "testing_mode"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-skip-params",
        literals: &[
            "skip_validation",
            "skip_verify",
            "skip_check",
            "skip_auth",
            "skip_api",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-mock-params",
        literals: &[
            "mock_mode",
            "fake_mode",
            "stub_mode",
            "use_mock",
            "use_fake",
            "use_stub",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    },
    NativeScanCheck {
        name: "no-test-flags-testing-feature",
        literals: &["#[cfg(feature = \"testing\")]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "no-test-flags-cfg-not-test",
        literals: &["#[cfg(not(test))]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "audit-no-serial-src",
        literals: &["#[serial]"],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-test-helpers-src",
        literals: &[
            "use test_helpers::",
            "init_git_repo",
            "commit_all",
            "git_switch",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── tests/integration_tests group ────────────────────────────────────────
    NativeScanCheck {
        name: "compliance-no-process-spawn",
        literals: &["std::process::Command::new", "assert_cmd::Command::new"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &["_TEMPLATE.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "compliance-no-serial",
        literals: &["#[serial]", "use serial_test"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &["_TEMPLATE.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    NativeScanCheck {
        name: "audit-no-cfg-test-integration",
        literals: &["cfg!(test)"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-real-fs-integration",
        literals: &["std::fs::", "TempDir", "tempfile::"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-real-process-integration",
        literals: &["std::process::Command::new"],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-env-mutations-integration",
        literals: &[
            "std::env::set_var",
            "std::env::remove_var",
            "env::set_var",
            "env::remove_var",
        ],
        directories: &["tests/integration_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── tests/process_system_tests group ─────────────────────────────────────
    NativeScanCheck {
        name: "audit-no-serial-process-system",
        literals: &["#[serial]"],
        directories: &["tests/process_system_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    },
    NativeScanCheck {
        name: "audit-no-git2-process-system",
        literals: &["git2::", "init_git_repo"],
        directories: &["tests/process_system_tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── ralph-workflow/src/reducer/handler/ group ─────────────────────────────
    NativeScanCheck {
        name: "no-string-errors-handlers",
        literals: &[
            "anyhow::anyhow!(",
            "anyhow!(",
            "anyhow::bail!(",
            "bail!(",
            "anyhow::ensure!(",
            "ensure!(",
            "anyhow::format_err!(",
            "format_err!(",
            "anyhow::Error::msg(",
        ],
        directories: &["ralph-workflow/src/reducer/handler"],
        include_glob: "*.rs",
        // Exclude test subdirectories (mirrors rg --glob !**/tests/**)
        exclude_globs: &["**/tests/**"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    },
    // ── audit-ignore-has-url: replaces PCRE2 rg negative lookahead ────────────
    // Fails if #[ignore] appears without an https:// URL on the same line.
    // The word_boundary_at_end check prevents #[ignore_reason] from triggering.
    NativeScanCheck {
        name: "audit-ignore-has-url",
        literals: &["#[ignore"],
        directories: &["tests", "ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    },
    // ── forbidden-allow-expect-scan: replaces PCRE2 rg multiline pattern ──────
    // Fails if #[allow(, #![allow(, #[expect(, #![expect(, #[cfg_attr(, or #![cfg_attr(
    // appears at line start (possibly preceded by whitespace). Comment lines are skipped.
    // Note: #[cfg_attr( and #![cfg_attr( are detected but require allow( or expect( on the
    // same line to be flagged as violations (to avoid false positives on regular cfg_attr usage).
    NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
            "ralph-gui",
            "lints",
        ],
        include_glob: "*.rs",
        exclude_globs: LINT_SCAN_EXCLUDE_GLOBS,
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    },
];
