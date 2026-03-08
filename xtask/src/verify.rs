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
    /// Environment variable overrides for this command.
    pub extra_env: &'static [(&'static str, &'static str)],
}

#[derive(Debug, Clone)]
pub struct CommandOutput {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

pub trait CommandRunner: Sync {
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

pub const NATIVE_REQUIRED_CHECKS: &[NativeCheck] = &[
    NativeCheck {
        name: "compliance-timeout-wrapper",
        run: crate::compliance::check_timeout_wrappers,
    },
    NativeCheck {
        name: "audit-no-shell-scripts",
        run: crate::compliance::check_no_shell_scripts,
    },
];

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

pub fn run_checks(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
) -> Result<VerifyReport> {
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

/// Index in REQUIRED_CHECKS where the rg fast-scan group ends (exclusive).
/// Checks at indices [0, RG_SCAN_END) are independent rg pattern scans (complex
/// PCRE2 patterns that cannot be expressed as Aho-Corasick literals) and can
/// run concurrently. Checks at indices [RG_SCAN_END, ..) invoke cargo and must
/// run sequentially to avoid target/ lock conflicts.
pub const RG_SCAN_END: usize = 2;

/// Run checks concurrently using scoped threads.
///
/// All checks run to completion. If any fail, the first failure
/// (by check-list order) is returned. Succeeds only if all checks pass.
pub fn run_checks_concurrent(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
) -> Result<VerifyReport> {
    use std::sync::Mutex;

    // Collect results in order-preserving slots.
    let slots: Vec<Mutex<Option<(CheckStatus, CommandOutput)>>> =
        (0..checks.len()).map(|_| Mutex::new(None)).collect();

    std::thread::scope(|s| {
        for (i, spec) in checks.iter().enumerate() {
            let slot = &slots[i];
            s.spawn(move || {
                let output = match runner.run(spec) {
                    Ok(o) => o,
                    Err(e) => {
                        *slot.lock().unwrap() = Some((
                            CheckStatus::Error,
                            CommandOutput {
                                exit_code: -1,
                                stdout: String::new(),
                                stderr: e.to_string(),
                            },
                        ));
                        return;
                    }
                };
                let status = classify(
                    output.exit_code,
                    &output.stdout,
                    &output.stderr,
                    spec.success_exit_codes,
                );
                *slot.lock().unwrap() = Some((status, output));
            });
        }
    });

    // Return first failure in original check order.
    for (i, spec) in checks.iter().enumerate() {
        let pair = slots[i]
            .lock()
            .unwrap()
            .take()
            .ok_or_else(|| anyhow::anyhow!("check {} did not produce a result", spec.name))?;
        let (status, output) = pair;
        if status != CheckStatus::Pass {
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

    Ok(VerifyReport {
        exit: VerifyExitCode::Success,
        failure: None,
    })
}

/// Format native scan violations in rg-compatible `path:line:content` format.
fn format_scan_violations(violations: &[crate::scanner::NativeScanViolation]) -> String {
    violations
        .iter()
        .map(|v| format!("{}:{}:{}", v.file.display(), v.line_number, v.line))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Fast verification: rg checks run concurrently, cargo checks via prefetch + sequential.
///
/// `prefetch_specs` are run concurrently in a background thread during the cargo phase to
/// pre-populate the cache (see `CachingCommandRunner`).  Pass `&[]` when no prefetch is
/// desired (e.g., in tests with fake runners).
pub fn verify_fast(
    runner: &(dyn CommandRunner + Sync),
    repo_root: &std::path::Path,
    native_checks: &[NativeCheck],
    checks: &[CommandSpec],
    prefetch_specs: &[CommandSpec],
) -> Result<VerifyReport> {
    // Phase 0: native checks (always sequential, very fast).
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

    // Phase 0.5: native Aho-Corasick multi-pattern scan (replaces 17 rg subprocess calls).
    // Groups checks by directory, reads each source file once, O(n + m + z) per group.
    let scan_results =
        crate::scanner::run_native_scan_checks(repo_root, crate::scanner::NATIVE_SCAN_CHECKS);
    for result in &scan_results {
        if !result.passed {
            let output = format_scan_violations(&result.violations);
            return Ok(VerifyReport {
                exit: VerifyExitCode::Failure,
                failure: Some(CheckFailure {
                    name: result.check_name,
                    status: CheckStatus::Error,
                    exit_code: 1,
                    stdout: output,
                    stderr: String::new(),
                }),
            });
        }
    }

    let rg_end = RG_SCAN_END.min(checks.len());
    let (rg_checks, cargo_checks) = checks.split_at(rg_end);

    // Phase 1: parallel rg pattern scans (complex PCRE2 checks only).
    let rg_report = run_checks_concurrent(runner, rg_checks)?;
    if rg_report.exit == VerifyExitCode::Failure {
        return Ok(rg_report);
    }

    // Phase 2: cargo checks with optional concurrent prefetch.
    run_cargo_prefetch(runner, prefetch_specs, cargo_checks)
}

pub const REQUIRED_CHECKS: &[CommandSpec] = &[
    // ── rg pattern scans (PCRE2 patterns that cannot be expressed as Aho-Corasick literals) ──
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
        extra_env: &[],
    },
    CommandSpec {
        // PCRE2 negative lookahead: cannot be expressed as Aho-Corasick literals.
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
        extra_env: &[],
    },
    // ── format and lint ──────────────────────────────────────────────────────
    CommandSpec {
        name: "fmt-check",
        program: "cargo",
        args: &["fmt", "--all", "--check"],
        success_exit_codes: &[0],
        extra_env: &[],
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
        extra_env: &[],
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
        extra_env: &[],
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
        extra_env: &[],
    },
    CommandSpec {
        name: "clippy-xtask",
        program: "cargo",
        args: &["clippy", "-p", "xtask", "--all-targets", "--", "-D", "warnings"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    // ── tests ────────────────────────────────────────────────────────────────
    CommandSpec {
        name: "test-xtask",
        program: "cargo",
        args: &["test", "-p", "xtask"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    CommandSpec {
        name: "test-ralph-workflow-lib",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "--all-features"],
        success_exit_codes: &[0],
        extra_env: &[],
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
        extra_env: &[],
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
        extra_env: &[],
    },
    CommandSpec {
        name: "memory-safety-benchmarks",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "benchmarks"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    CommandSpec {
        name: "memory-safety-executor",
        program: "cargo",
        args: &["test", "-p", "ralph-workflow", "--lib", "executor::tests"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    // ── release build and custom lints ───────────────────────────────────────
    CommandSpec {
        name: "release-build",
        program: "cargo",
        args: &["build", "--release"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    CommandSpec {
        name: "dylint",
        program: "make",
        args: &["dylint"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
];

/// Prefetch specs for xtask checks run concurrently with the sequential cargo phase.
///
/// These use the same `name` as the corresponding entries in REQUIRED_CHECKS so they
/// share the same cache key (name + scope hash).  A separate `CARGO_TARGET_DIR` avoids
/// holding the default target/ lock while the main sequential phase compiles other crates.
pub const CARGO_PREFETCH_SPECS: &[CommandSpec] = &[
    CommandSpec {
        name: "clippy-xtask",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "xtask",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
        extra_env: &[("CARGO_TARGET_DIR", "target/xtask-parallel-verify")],
    },
    CommandSpec {
        name: "test-xtask",
        program: "cargo",
        args: &["test", "-p", "xtask"],
        success_exit_codes: &[0],
        extra_env: &[("CARGO_TARGET_DIR", "target/xtask-parallel-verify")],
    },
];

/// Run prefetch specs concurrently with main specs.
///
/// Launches a background thread that runs `prefetch_specs` while the current
/// thread runs `main_specs` sequentially.  When `prefetch_specs` complete they
/// populate the `CachingCommandRunner` cache so that, if the sequential phase
/// later reaches the same check names, it gets instant cache hits.
///
/// Returns the first failure in `main_specs` order.  If `main_specs` all pass
/// but a prefetch spec fails, the prefetch failure is returned.
pub fn run_cargo_prefetch(
    runner: &(dyn CommandRunner + Sync),
    prefetch_specs: &[CommandSpec],
    main_specs: &[CommandSpec],
) -> Result<VerifyReport> {
    use std::sync::Mutex;

    // Slot for the prefetch thread's combined report.
    let prefetch_result: Mutex<Option<VerifyReport>> = Mutex::new(None);

    std::thread::scope(|s| {
        // Spawn background prefetch thread.
        s.spawn(|| {
            let report = run_checks(runner, prefetch_specs).unwrap_or(VerifyReport {
                exit: VerifyExitCode::Success,
                failure: None,
            });
            *prefetch_result.lock().unwrap() = Some(report);
        });

        // Main thread runs main specs sequentially.
        let main_report = run_checks(runner, main_specs)?;

        // Wait for prefetch to finish (scope join).
        Ok::<_, anyhow::Error>(main_report)
    })
    .map(|main_report| {
        if main_report.exit == VerifyExitCode::Failure {
            return main_report;
        }
        // Main passed; check if prefetch had a failure.
        prefetch_result
            .into_inner()
            .unwrap()
            .unwrap_or(VerifyReport {
                exit: VerifyExitCode::Success,
                failure: None,
            })
    })
}

#[cfg(test)]
fn verify(
    runner: &(dyn CommandRunner + Sync),
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

    use std::collections::VecDeque;
    use std::sync::Mutex;

    #[derive(Debug, Default)]
    struct FakeRunner {
        outputs: Mutex<VecDeque<CommandOutput>>,
    }

    impl FakeRunner {
        fn new(outputs: impl IntoIterator<Item = CommandOutput>) -> Self {
            Self {
                outputs: Mutex::new(outputs.into_iter().collect()),
            }
        }
    }

    impl CommandRunner for FakeRunner {
        fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.outputs
                .lock()
                .unwrap()
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
            extra_env: &[],
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
            extra_env: &[],
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
        ran: Mutex<Vec<&'static str>>,
    }

    impl RecordingRunner {
        fn ran(&self) -> Vec<&'static str> {
            self.ran.lock().unwrap().clone()
        }
    }

    impl CommandRunner for RecordingRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.ran.lock().unwrap().push(spec.name);

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
        // After the Aho-Corasick migration, REQUIRED_CHECKS contains only the 2 complex
        // PCRE2 rg checks and the cargo checks.  The 17 literal pattern checks and
        // audit-no-shell-scripts are now handled natively (no CommandRunner involvement).
        assert_eq!(
            runner.ran(),
            vec![
                // PCRE2 rg checks (complex patterns kept as rg)
                "forbidden-allow-expect-scan",
                "audit-ignore-has-url",
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
    fn test_no_string_errors_handlers_check_is_in_native_scan_checks() {
        // TDD anchor: no-string-errors-handlers is now a native Aho-Corasick scan check,
        // not a rg subprocess check.  Verify it is registered in NATIVE_SCAN_CHECKS.
        assert!(
            crate::scanner::NATIVE_SCAN_CHECKS
                .iter()
                .any(|c| c.name == "no-string-errors-handlers"),
            "NATIVE_SCAN_CHECKS must include the no-string-errors-handlers audit check"
        );
    }

    #[test]
    fn test_audit_no_shell_scripts_check_is_in_native_required_checks() {
        // TDD anchor: audit-no-shell-scripts is now a NativeCheck (file-existence check),
        // not a rg subprocess check.  Verify it is registered in NATIVE_REQUIRED_CHECKS.
        assert!(
            NATIVE_REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "audit-no-shell-scripts"),
            "NATIVE_REQUIRED_CHECKS must include the audit-no-shell-scripts regression guard"
        );
    }

    #[test]
    fn test_audit_ignore_has_url_check_is_in_required_checks() {
        // TDD anchor: audit-ignore-has-url uses PCRE2 negative lookahead and must stay as rg.
        assert!(
            REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "audit-ignore-has-url"),
            "REQUIRED_CHECKS must include the audit-ignore-has-url rg check (complex PCRE2)"
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

        // Confirm the expected native checks are present
        assert!(
            NATIVE_REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "compliance-timeout-wrapper"),
            "compliance-timeout-wrapper native check must be registered"
        );
        assert!(
            NATIVE_REQUIRED_CHECKS
                .iter()
                .any(|c| c.name == "audit-no-shell-scripts"),
            "audit-no-shell-scripts native check must be registered"
        );
    }

    // ── TDD tests for concurrent execution ──────────────────────────────────

    #[test]
    fn test_rg_scan_end_points_to_fmt_check() {
        // TDD anchor: RG_SCAN_END must point exactly at the first cargo check.
        assert!(
            RG_SCAN_END < REQUIRED_CHECKS.len(),
            "RG_SCAN_END must be within REQUIRED_CHECKS bounds"
        );
        assert_eq!(
            REQUIRED_CHECKS[RG_SCAN_END].name, "fmt-check",
            "REQUIRED_CHECKS[RG_SCAN_END] must be fmt-check (first cargo check)"
        );
    }

    #[test]
    fn test_rg_scan_group_contains_only_rg_checks() {
        // TDD anchor: all checks before RG_SCAN_END must use program == "rg".
        for spec in &REQUIRED_CHECKS[..RG_SCAN_END] {
            assert_eq!(
                spec.program, "rg",
                "check '{}' at index before RG_SCAN_END must use program 'rg', got '{}'",
                spec.name, spec.program
            );
        }
    }

    #[test]
    fn test_run_checks_concurrent_runs_all_checks_when_all_pass() {
        // Arrange: N checks that all succeed
        let checks = [
            CommandSpec {
                name: "check-a",
                program: "rg",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
            CommandSpec {
                name: "check-b",
                program: "rg",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
            CommandSpec {
                name: "check-c",
                program: "rg",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
        ];
        let runner = RecordingRunner::default();

        // Act
        let report =
            run_checks_concurrent(&runner, &checks).expect("concurrent checks should not error");

        // Assert
        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);
        let ran = runner.ran();
        // All checks must have run (order may differ due to parallelism)
        use std::collections::HashSet;
        let ran_set: HashSet<&str> = ran.iter().copied().collect();
        assert!(ran_set.contains("check-a"), "check-a must have run");
        assert!(ran_set.contains("check-b"), "check-b must have run");
        assert!(ran_set.contains("check-c"), "check-c must have run");
    }

    #[test]
    fn test_run_checks_concurrent_collects_first_failure() {
        // Arrange: both checks fail so that the result is deterministic even with concurrent
        // thread scheduling. When both fail, run_checks_concurrent always returns the first
        // failure BY CHECK-LIST ORDER (not by completion order), which is "fail-check".
        let checks = [
            CommandSpec {
                name: "fail-check",
                program: "rg",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
            CommandSpec {
                name: "second-fail-check",
                program: "rg",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
        ];
        let runner = FakeRunner::new([
            CommandOutput {
                exit_code: 42,
                stdout: "matches found".to_string(),
                stderr: String::new(),
            },
            CommandOutput {
                exit_code: 42,
                stdout: "also failing".to_string(),
                stderr: String::new(),
            },
        ]);

        // Act
        let report =
            run_checks_concurrent(&runner, &checks).expect("concurrent checks should not error");

        // Assert: even though both checks fail concurrently, the FIRST failure by check-list
        // order is returned — "fail-check" at index 0, not "second-fail-check" at index 1.
        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure details");
        assert_eq!(failure.name, "fail-check");
    }

    #[test]
    fn test_verify_fast_runs_all_required_checks() {
        // TDD anchor: verify_fast() must exercise all REQUIRED_CHECKS.
        let runner = RecordingRunner::default();
        let report = verify_fast(
            &runner,
            std::path::Path::new("/fake"),
            NATIVE_REQUIRED_CHECKS,
            REQUIRED_CHECKS,
            &[], // no prefetch in tests
        )
        .expect("verify_fast should not error");

        assert_eq!(report.exit, VerifyExitCode::Success);
        assert_eq!(report.failure, None);

        let ran = runner.ran();
        use std::collections::HashSet;
        let ran_set: HashSet<&str> = ran.iter().copied().collect();

        for spec in REQUIRED_CHECKS {
            assert!(
                ran_set.contains(spec.name),
                "verify_fast must run check '{}'",
                spec.name
            );
        }
    }

    #[test]
    fn test_verify_fast_stops_on_rg_group_failure() {
        // TDD anchor: when an rg check fails, cargo checks must NOT run.
        // All rg checks fail so the first failure by check-list order is deterministic:
        // concurrent threads may consume outputs in any order, but since every rg output
        // is a failure, run_checks_concurrent always returns the first failure BY ORDER
        // ("forbidden-allow-expect-scan" at index 0).
        let mut outputs: Vec<CommandOutput> = Vec::new();
        // All rg checks fail (exit 0 = matches found = failure for rg success_exit_codes: &[1])
        for _ in 0..RG_SCAN_END {
            outputs.push(CommandOutput {
                exit_code: 0,
                stdout: "found forbidden pattern".to_string(),
                stderr: String::new(),
            });
        }
        let runner = FakeRunner::new(outputs);

        let report = verify_fast(
            &runner,
            std::path::Path::new("/fake"),
            NATIVE_REQUIRED_CHECKS,
            REQUIRED_CHECKS,
            &[], // no prefetch in tests
        )
        .expect("verify_fast should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure");
        assert_eq!(failure.name, "forbidden-allow-expect-scan");
    }

    #[test]
    fn test_verify_fast_stops_on_cargo_check_failure() {
        // TDD anchor: when first cargo check (fmt-check) fails, later cargo checks must not run.
        let mut outputs: Vec<CommandOutput> = Vec::new();
        // All rg checks pass
        for _ in 0..RG_SCAN_END {
            outputs.push(CommandOutput {
                exit_code: 1, // no matches = success
                stdout: String::new(),
                stderr: String::new(),
            });
        }
        // fmt-check fails
        outputs.push(CommandOutput {
            exit_code: 1, // exit 1 = failure for cargo (success_exit_codes: &[0])
            stdout: String::new(),
            stderr: "error: formatting differences found".to_string(),
        });
        let runner = FakeRunner::new(outputs);

        let report = verify_fast(
            &runner,
            std::path::Path::new("/fake"),
            NATIVE_REQUIRED_CHECKS,
            REQUIRED_CHECKS,
            &[], // no prefetch in tests
        )
        .expect("verify_fast should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure");
        assert_eq!(failure.name, "fmt-check");
    }

    // ── TDD tests for cargo prefetch ────────────────────────────────────────

    #[test]
    fn test_cargo_prefetch_specs_use_same_names_as_required_checks() {
        // TDD anchor: CARGO_PREFETCH_SPECS must use the same check names as REQUIRED_CHECKS
        // so the cache key (name + scope hash) is shared between prefetch and sequential runs.
        for spec in CARGO_PREFETCH_SPECS {
            assert!(
                REQUIRED_CHECKS.iter().any(|c| c.name == spec.name),
                "prefetch spec '{}' must have a matching entry in REQUIRED_CHECKS",
                spec.name
            );
        }
    }

    #[test]
    fn test_cargo_prefetch_specs_have_extra_env() {
        // TDD anchor: prefetch specs must carry CARGO_TARGET_DIR override to avoid lock
        // contention with the main sequential build.
        for spec in CARGO_PREFETCH_SPECS {
            let has_cargo_target_dir = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TARGET_DIR");
            assert!(
                has_cargo_target_dir,
                "prefetch spec \'{}\' must set CARGO_TARGET_DIR in extra_env",
                spec.name
            );
        }
    }

    #[test]
    fn test_run_cargo_prefetch_populates_results_for_all_specs() {
        // TDD anchor: run_cargo_prefetch must run all prefetch specs and all main specs,
        // returning Success when all pass.
        let prefetch_names: Vec<&str> = CARGO_PREFETCH_SPECS.iter().map(|s| s.name).collect();

        let runner = RecordingRunner::default();

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "fmt-check",
            program: "cargo",
            args: &["fmt", "--all", "--check"],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let prefetch_specs = CARGO_PREFETCH_SPECS;

        let report = run_cargo_prefetch(&runner, prefetch_specs, main_specs)
            .expect("run_cargo_prefetch should not error");

        assert_eq!(report.exit, VerifyExitCode::Success);

        let ran = runner.ran();
        assert!(
            ran.contains(&"fmt-check"),
            "run_cargo_prefetch must run main specs"
        );
        for name in &prefetch_names {
            assert!(
                ran.contains(name),
                "run_cargo_prefetch must run prefetch spec \'{name}\'"
            );
        }
    }

    #[test]
    fn test_run_cargo_prefetch_returns_failure_on_main_spec_failure() {
        // TDD anchor: if a main spec fails, run_cargo_prefetch must return Failure.
        let outputs = vec![CommandOutput {
            exit_code: 1, // exit 1 = failure for cargo
            stdout: String::new(),
            stderr: "error: formatting differences found".to_string(),
        }];
        let runner = FakeRunner::new(outputs);

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "fmt-check-fail",
            program: "cargo",
            args: &["fmt", "--all", "--check"],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let report = run_cargo_prefetch(&runner, &[], main_specs)
            .expect("run_cargo_prefetch should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure");
        assert_eq!(failure.name, "fmt-check-fail");
    }
}
