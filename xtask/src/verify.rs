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

const REQUIRED_CHECKS: &[CommandSpec] = &[
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
        // Exit code 1 means "no matches".
        success_exit_codes: &[1],
    },
    CommandSpec {
        name: "integration-test-compliance",
        program: "bash",
        args: &["./tests/integration_tests/compliance_check.sh"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "no-test-flags",
        program: "bash",
        args: &["./tests/integration_tests/no_test_flags_check.sh"],
        success_exit_codes: &[0],
    },
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
    CommandSpec {
        name: "audit-tests",
        program: "bash",
        args: &["scripts/audit_tests.sh"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "verify-memory-safety",
        program: "bash",
        args: &["scripts/verify_memory_safety.sh"],
        success_exit_codes: &[0],
    },
    CommandSpec {
        name: "ci-performance-regression",
        program: "bash",
        args: &["scripts/ci_performance_regression.sh"],
        success_exit_codes: &[0],
    },
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

pub fn verify(runner: &dyn CommandRunner) -> Result<VerifyReport> {
    run_checks(runner, REQUIRED_CHECKS)
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

    #[test]
    fn test_verify_runs_required_checks_in_stable_order() {
        // Arrange
        let runner = RecordingRunner::default();

        // Act
        let report = verify(&runner).expect("verify should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
        assert_eq!(
            runner.ran(),
            vec![
                "forbidden-allow-expect-scan",
                "integration-test-compliance",
                "no-test-flags",
                "fmt-check",
                "clippy-ralph-workflow",
                "clippy-ralph-workflow-tests",
                "clippy-test-helpers",
                "clippy-xtask",
                "test-xtask",
                "test-ralph-workflow-lib",
                "test-integration",
                "audit-tests",
                "verify-memory-safety",
                "ci-performance-regression",
                "release-build",
                "dylint",
            ]
        );
    }
}
