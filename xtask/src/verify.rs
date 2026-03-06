use anyhow::{Context as _, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerifyExitCode {
    Success = 0,
    Failure = 1,
}

#[derive(Debug, Clone)]
pub struct CommandSpec {
    pub name: &'static str,
    pub program: &'static str,
    pub args: &'static [&'static str],
    pub success_exit_codes: &'static [i32],
}

#[derive(Debug, Clone)]
pub struct CommandOutput {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

pub trait CommandRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckStatus {
    Pass,
    Warning,
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckFailure {
    pub name: &'static str,
    pub status: CheckStatus,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifyReport {
    pub exit: VerifyExitCode,
    pub failure: Option<CheckFailure>,
}

pub struct NativeCheckResult {
    pub status: CheckStatus,
    pub message: String,
}

pub struct NativeCheck {
    pub name: &'static str,
    pub run: fn(&std::path::Path) -> NativeCheckResult,
}

pub const NATIVE_REQUIRED_CHECKS: &[NativeCheck] = &[NativeCheck {
    name: "compliance-timeout-wrapper",
    run: crate::compliance::check_timeout_wrappers,
}];

fn has_line_prefix(output: &str, prefix: &str) -> bool {
    output
        .lines()
        .any(|line| line.trim_start().starts_with(prefix))
}

fn classify(exit_code: i32, stdout: &str, stderr: &str, success_exit_codes: &[i32]) -> CheckStatus {
    if !success_exit_codes.contains(&exit_code) {
        return CheckStatus::Error;
    }

    if has_line_prefix(stderr, "error:")
        || has_line_prefix(stdout, "error:")
        || has_line_prefix(stderr, "Error:")
        || has_line_prefix(stdout, "Error:")
    {
        return CheckStatus::Error;
    }

    if has_line_prefix(stderr, "warning:") || has_line_prefix(stdout, "warning:") {
        return CheckStatus::Warning;
    }

    CheckStatus::Pass
}

pub fn run_checks(runner: &dyn CommandRunner, checks: &[CommandSpec]) -> Result<VerifyReport> {
    for spec in checks {
        let output = runner
            .run(spec)
            .with_context(|| format!("run {}", spec.name))?;

        let status = classify(
            output.exit_code,
            &output.stdout,
            &output.stderr,
            spec.success_exit_codes,
        );

        match status {
            CheckStatus::Pass => {}
            CheckStatus::Warning | CheckStatus::Error => {
                return Ok(VerifyReport {
                    exit: VerifyExitCode::Failure,
                    failure: Some(CheckFailure {
                        name: spec.name,
                        status,
                        exit_code: output.exit_code,
                        stdout: output.stdout,
                        stderr: output.stderr,
                    }),
                });
            }
        }
    }

    Ok(VerifyReport {
        exit: VerifyExitCode::Success,
        failure: None,
    })
}

pub const REQUIRED_CHECKS: &[CommandSpec] = &[
    CommandSpec {
        name: "forbidden-allow-expect-scan",
        program: "rg",
        args: &[
            "-n",
            "-U",
            "--pcre2",
            "(?m)^\\s*#\\s*!?\\[\\s*(?:(?:allow|expect)\\s*\\(|cfg_attr\\s*\\((?:[^()]|\\([^()]*\\))*?,\\s*(?:allow|expect)\\s*\\()",
            "--glob",
            "!target/**",
            "--glob",
            "!.git/**",
            "--glob",
            "*.rs",
            ".",
        ],
        // Exit code 1 means "no matches" which is success.
        success_exit_codes: &[1],
    },
    // ── no-test-flags group (replaces no_test_flags_check.sh) ──────────────
    CommandSpec {
        name: "no-test-flags-cfg-test",
        program: "rg",
        args: &[
            "-n",
            r"cfg!\(test\)",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-test-flags-test-mode-params",
        program: "rg",
        args: &[
            "-n",
            r"(test_mode|is_test|is_testing|testing_mode)\s*:\s*bool",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-test-flags-skip-params",
        program: "rg",
        args: &[
            "-n",
            r"(skip_validation|skip_verify|skip_check|skip_auth|skip_api)\s*:\s*bool",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-test-flags-mock-params",
        program: "rg",
        args: &[
            "-n",
            r"(mock_mode|fake_mode|stub_mode|use_mock|use_fake|use_stub)\s*:\s*bool",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-test-flags-testing-feature",
        program: "rg",
        args: &[
            "-n",
            r#"#\[cfg\(feature\s*=\s*"testing"\)\]"#,
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-test-flags-cfg-not-test",
        program: "rg",
        args: &[
            "-n",
            r"#\[cfg\(not\(test\)\)\]",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    // ── compliance group (process-spawn and serial checks from compliance_check.sh) ──
    CommandSpec {
        name: "compliance-no-process-spawn",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines (// prefix) to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*(std::process::Command::new|assert_cmd::Command::new)",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
            "--glob",
            "!_TEMPLATE.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "compliance-no-serial",
        program: "rg",
        args: &[
            "-n",
            r"#\[serial\]|use serial_test",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
            "--glob",
            "!_TEMPLATE.rs",
        ],
        success_exit_codes: &[1],
    },
    // ── audit group (replaces audit_tests.sh critical gates) ────────────────
    CommandSpec {
        name: "audit-no-cfg-test-integration",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*cfg!\(test\)",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-real-fs-integration",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*(std::fs::|TempDir|tempfile::)",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-real-process-integration",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*std::process::Command::new",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-serial-src",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*#\[serial\]",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-test-helpers-src",
        program: "rg",
        args: &[
            "-n",
            r"use test_helpers::|init_git_repo|commit_all|git_switch",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-env-mutations-integration",
        program: "rg",
        args: &[
            "-n",
            r"std::env::set_var|std::env::remove_var|env::set_var|env::remove_var",
            "tests/integration_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-serial-process-system",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            // Exclude comment lines to avoid false positives from doc comments.
            r"^(?!\s*//)[^\n]*#\[serial\]",
            "tests/process_system_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-no-git2-process-system",
        program: "rg",
        args: &[
            "-n",
            r"git2::|init_git_repo",
            "tests/process_system_tests/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "audit-ignore-has-url",
        program: "rg",
        args: &[
            "--pcre2",
            "-n",
            r"#\[ignore\b(?!.*https://)",
            "tests/",
            "ralph-workflow/src/",
            "--glob",
            "*.rs",
        ],
        success_exit_codes: &[1],
    },
    // Regression guard: no .sh files may be committed after the shell-script migration.
    // rg --files exits 1 (no matches) = pass; exits 0 (matches found) = fail.
    CommandSpec {
        name: "audit-no-shell-scripts",
        program: "rg",
        args: &[
            "--files",
            "--glob",
            "*.sh",
            "--glob",
            "!target/**",
            "--glob",
            "!.git/**",
            ".",
        ],
        // Exit code 1 means "no matches found" which is the success condition.
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "no-string-errors-handlers",
        program: "rg",
        args: &[
            "-n",
            "--pcre2",
            r"\banyhow::anyhow!\(|\banyhow!\(|\banyhow::bail!\(|\bbail!\(|\banyhow::ensure!\(|\bensure!\(|\banyhow::format_err!\(|\bformat_err!\(|\banyhow::Error::msg\(",
            "ralph-workflow/src/reducer/handler/",
            "--glob",
            "!**/tests/**",
        ],
        // Exit code 1 means "no matches" which is success.
        success_exit_codes: &[1],
    },
    // ── format and lint ──────────────────────────────────────────────────────
    CommandSpec {
        name: "fmt-check",
        program: "cargo",
        args: &["fmt", "--all", "--check"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "clippy-ralph-workflow",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "ralph-workflow",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "clippy-ralph-workflow-tests",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "ralph-workflow-tests",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "clippy-test-helpers",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "test-helpers",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "clippy-xtask",
        program: "cargo",
        args: &["clippy", "-p", "xtask", "--all-targets", "--", "-D", "warnings"],
        success_exit_codes: &[0],
    },
    // ── tests ────────────────────────────────────────────────────────────────
    CommandSpec {
        name: "test-xtask",
        program: "cargo",
        args: &["test", "-p", "xtask"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "test-ralph-workflow-lib",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "--all-features"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "test-integration",
        program: "cargo",
        args: &[
            "test",
            "-p",
            "ralph-workflow-tests",
            "--test",
            "integration_tests",
        ],
        success_exit_codes: &[0],
    },
    // ── memory safety (replaces verify_memory_safety.sh and ci_performance_regression.sh) ──
    CommandSpec {
        name: "memory-safety-integration",
        program: "cargo",
        args: &[
            "test",
            "-p",
            "ralph-workflow-tests",
            "--test",
            "integration_tests",
            "memory_safety",
        ],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "memory-safety-benchmarks",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "benchmarks"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "memory-safety-executor",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "executor::tests"],
        success_exit_codes: &[0],
    },
    // ── release build and custom lints ───────────────────────────────────────
    CommandSpec {
        name: "release-build",
        program: "cargo",
        args: &["build", "--release"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "dylint",
        program: "make",
        args: &["dylint"],
        success_exit_codes: &[0],
    },
];

pub fn verify(
    runner: &dyn CommandRunner,
    repo_root: &std::path::Path,
    native_checks: &[NativeCheck],
    checks: &[CommandSpec],
) -> Result<VerifyReport> {
    // Run native checks first (Rust-native, no external subprocess)
    for check in native_checks {
        let result = (check.run)(repo_root);
        if result.status != CheckStatus::Pass {
            return Ok(VerifyReport {
                exit: VerifyExitCode::Failure,
                failure: Some(CheckFailure {
                    name: check.name,
                    status: result.status,
                    exit_code: -1,
                    stdout: result.message,
                    stderr: String::new(),
                }),
            });
        }
    }

    run_checks(runner, checks)
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::cell::RefCell;
    use std::collections::VecDeque;

    #[derive(Debug, Default)]
    struct FakeRunner {
        outputs: RefCell<VecDeque<CommandOutput>>,
    }

    impl FakeRunner {
        fn new(outputs: impl IntoIterator<Item = CommandOutput>) -> Self {
            Self {
                outputs: RefCell::new(outputs.into_iter().collect()),
            }
        }
    }

    impl CommandRunner for FakeRunner {
        fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.outputs
                .borrow_mut()
                .pop_front()
                .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "no output"))
        }
    }

    fn check(name: &'static str) -> CommandSpec {
        CommandSpec {
            name,
            program: "fake",
            args: &[],
            success_exit_codes: &[0],
        }
    }

    #[test]
    fn test_verify_succeeds_when_all_checks_have_allowed_exit_code_and_empty_stderr() {
        // Arrange
        let runner = FakeRunner::new([
            CommandOutput {
                exit_code: 0,
                stdout: "ok".to_string(),
                stderr: String::new(),
            },
            CommandOutput {
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            },
        ]);
        let checks = [check("a"), check("b")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should succeed");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
    }

    #[test]
    fn test_verify_succeeds_when_exit_code_is_nonzero_but_allowed() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 1,
            stdout: String::new(),
            stderr: String::new(),
        }]);
        let checks = [CommandSpec {
            name: "rg-no-matches",
            program: "rg",
            args: &["pattern"],
            success_exit_codes: &[1],
        }];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should succeed");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
    }

    #[test]
    fn test_verify_fails_on_warning_diagnostic_even_if_exit_code_is_allowed() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: "warning: something".to_string(),
        }]);
        let checks = [check("a")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Failure);
    }

    #[test]
    fn test_verify_fails_on_error_diagnostic_even_if_exit_code_is_allowed() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: "error: something".to_string(),
        }]);
        let checks = [check("a")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Failure);
    }

    #[test]
    fn test_verify_returns_failure_details_when_exit_code_is_disallowed() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 42,
            stdout: "some stdout".to_string(),
            stderr: "some stderr".to_string(),
        }]);
        let checks = [check("disallowed-exit")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.name, "disallowed-exit");
        assert_eq!(failure.status, CheckStatus::Error);
        assert_eq!(failure.exit_code, 42);
        assert_eq!(failure.stdout, "some stdout");
        assert_eq!(failure.stderr, "some stderr");
    }

    #[test]
    fn test_verify_returns_failure_details_on_warning_diagnostic() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: "warning: something".to_string(),
        }]);
        let checks = [check("warning-check")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.name, "warning-check");
        assert_eq!(failure.status, CheckStatus::Warning);
        assert_eq!(failure.exit_code, 0);
        assert_eq!(failure.stdout, "");
        assert_eq!(failure.stderr, "warning: something");
    }

    #[test]
    fn test_verify_returns_failure_details_on_error_diagnostic() {
        // Arrange
        let runner = FakeRunner::new([CommandOutput {
            exit_code: 0,
            stdout: "Error: from stdout".to_string(),
            stderr: String::new(),
        }]);
        let checks = [check("error-check")];

        // Act
        let report = run_checks(&runner, &checks).expect("run_checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.name, "error-check");
        assert_eq!(failure.status, CheckStatus::Error);
        assert_eq!(failure.exit_code, 0);
        assert_eq!(failure.stdout, "Error: from stdout");
        assert_eq!(failure.stderr, "");
    }

    #[test]
    fn test_verify_exit_code_is_deterministic_for_identical_outputs() {
        // Arrange
        let outputs = [CommandOutput {
            exit_code: 0,
            stdout: "ok".to_string(),
            stderr: String::new(),
        }];
        let checks = [check("a")];
        let runner_a = FakeRunner::new(outputs.clone());
        let runner_b = FakeRunner::new(outputs);

        // Act
        let report_a = run_checks(&runner_a, &checks).expect("run_checks A should succeed");
        let report_b = run_checks(&runner_b, &checks).expect("run_checks B should succeed");

        // Assert
        assert_eq!(report_a, report_b);
    }

    #[derive(Debug, Default)]
    struct RecordingRunner {
        ran: RefCell<Vec<&'static str>>,
    }

    impl RecordingRunner {
        fn ran(&self) -> Vec<&'static str> {
            self.ran.borrow().clone()
        }
    }

    impl CommandRunner for RecordingRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.ran.borrow_mut().push(spec.name);

            Ok(CommandOutput {
                exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    fn fake_warning_native_check(_: &std::path::Path) -> NativeCheckResult {
        NativeCheckResult {
            status: CheckStatus::Warning,
            message: "warning: fake warning from native check".to_string(),
        }
    }

    fn fake_error_native_check(_: &std::path::Path) -> NativeCheckResult {
        NativeCheckResult {
            status: CheckStatus::Error,
            message: "error: fake error from native check".to_string(),
        }
    }

    #[test]
    fn test_verify_fails_immediately_when_native_check_returns_warning() {
        // TDD anchor: verifies that when a native check returns Warning, verify() returns Failure
        // and does NOT invoke any CommandRunner checks (early-exit guarantee).
        let runner = RecordingRunner::default();

        let warning_native_check = NativeCheck {
            name: "fake-native-warning",
            run: fake_warning_native_check,
        };

        let report = verify(
            &runner,
            std::path::Path::new("/fake"),
            &[warning_native_check],
            &[],
        )
        .expect("verify should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.name, "fake-native-warning");
        assert_eq!(failure.status, CheckStatus::Warning);
        // Runner must NOT have been called — native check failure causes early exit.
        assert!(
            runner.ran().is_empty(),
            "command specs must not run after a native check failure"
        );
    }

    #[test]
    fn test_verify_fails_immediately_when_native_check_returns_error() {
        // TDD anchor: companion to the Warning test above.
        let runner = RecordingRunner::default();

        let error_native_check = NativeCheck {
            name: "fake-native-error",
            run: fake_error_native_check,
        };

        let report = verify(
            &runner,
            std::path::Path::new("/fake"),
            &[error_native_check],
            &[],
        )
        .expect("verify should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.status, CheckStatus::Error);
        assert!(runner.ran().is_empty());
    }

    #[test]
    fn test_verify_succeeds_with_empty_native_and_command_checks() {
        // TDD anchor: verify() with no checks at all returns Success.
        let runner = RecordingRunner::default();

        let report = verify(&runner, std::path::Path::new("/fake"), &[], &[])
            .expect("verify should not error");

        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
    }

    #[test]
    fn test_verify_runs_required_checks_in_stable_order() {
        // Arrange
        let runner = RecordingRunner::default();

        // Act
        // Pass a nonexistent path so native checks (compliance-timeout-wrapper) return Pass
        let report = verify(
            &runner,
            std::path::Path::new("/fake"),
            NATIVE_REQUIRED_CHECKS,
            REQUIRED_CHECKS,
        )
        .expect("verify should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
        assert_eq!(
            runner.ran(),
            vec![
                // rg pattern checks
                "forbidden-allow-expect-scan",
                // no-test-flags group
                "no-test-flags-cfg-test",
                "no-test-flags-test-mode-params",
                "no-test-flags-skip-params",
                "no-test-flags-mock-params",
                "no-test-flags-testing-feature",
                "no-test-flags-cfg-not-test",
                // compliance group
                "compliance-no-process-spawn",
                "compliance-no-serial",
                // audit group
                "audit-no-cfg-test-integration",
                "audit-no-real-fs-integration",
                "audit-no-real-process-integration",
                "audit-no-serial-src",
                "audit-no-test-helpers-src",
                "audit-no-env-mutations-integration",
                "audit-no-serial-process-system",
                "audit-no-git2-process-system",
                "audit-ignore-has-url",
                "audit-no-shell-scripts",
                "no-string-errors-handlers",
                // format and lint
                "fmt-check",
                "clippy-ralph-workflow",
                "clippy-ralph-workflow-tests",
                "clippy-test-helpers",
                "clippy-xtask",
                // tests
                "test-xtask",
                "test-ralph-workflow-lib",
                "test-integration",
                // memory safety
                "memory-safety-integration",
                "memory-safety-benchmarks",
                "memory-safety-executor",
                // build and custom lints
                "release-build",
                "dylint",
            ]
        );
    }

    #[test]
    fn test_required_checks_do_not_invoke_shell_scripts() {
        for spec in REQUIRED_CHECKS {
            assert_ne!(
                spec.program, "bash",
                "verify steps must not invoke bash scripts: {}",
                spec.name
            );

            // Args ending in ".sh" are forbidden UNLESS they are glob patterns (contain '*').
            // The audit-no-shell-scripts check uses "*.sh" as an rg --glob pattern to detect
            // committed shell scripts — that is the search target, not an invocation.
            assert!(
                !spec
                    .args
                    .iter()
                    .any(|arg| arg.ends_with(".sh") && !arg.contains('*')),
                "verify steps must not reference .sh scripts: {} -> {:?}",
                spec.name,
                spec.args
            );
        }
    }

    #[test]
    fn test_no_string_errors_handlers_check_is_in_required_checks() {
        // TDD anchor: this test fails until the CommandSpec is added to REQUIRED_CHECKS
        assert!(
            REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "no-string-errors-handlers"),
            "REQUIRED_CHECKS must include the no-string-errors-handlers audit check"
        );
    }

    #[test]
    fn test_audit_no_shell_scripts_check_is_in_required_checks() {
        // TDD anchor: this test fails until audit-no-shell-scripts is added to REQUIRED_CHECKS.
        // Policy: no .sh files may exist in scripts/ or tests/integration_tests/.
        // This check prevents regression after the shell-script migration.
        assert!(
            REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "audit-no-shell-scripts"),
            "REQUIRED_CHECKS must include the audit-no-shell-scripts regression guard"
        );
    }

    #[test]
    fn test_native_checks_do_not_include_bash() {
        // Native checks are Rust functions — no bash invocations possible.
        // Verify at least one native check exists (compliance-timeout-wrapper).
        assert!(
            !NATIVE_REQUIRED_CHECKS.is_empty(),
            "at least one native check should be registered"
        );

        // Confirm the expected native check is present
        assert!(
            NATIVE_REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "compliance-timeout-wrapper"),
            "compliance-timeout-wrapper native check must be registered"
        );
    }
}
