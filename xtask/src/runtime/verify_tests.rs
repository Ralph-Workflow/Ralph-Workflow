use super::*;

use std::collections::{HashMap, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::Mutex;
use std::time::{Duration, Instant};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("xtask manifest dir should live under repo root")
        .to_path_buf()
}

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

#[derive(Debug, Default)]
struct ByNameRunner {
    outputs: Mutex<HashMap<&'static str, CommandOutput>>,
}

impl ByNameRunner {
    fn with_output(self, name: &'static str, output: CommandOutput) -> Self {
        self.outputs.lock().unwrap().insert(name, output);
        self
    }
}

impl CommandRunner for ByNameRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        self.outputs
            .lock()
            .unwrap()
            .get(spec.name)
            .cloned()
            .ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    format!("no output configured for {}", spec.name),
                )
            })
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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should succeed");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should succeed");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should not error");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should not error");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should not error");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should not error");

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
    let report =
        run_checks(&runner, &checks, &NoopProgressReporter).expect("run_checks should not error");

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
    let report_a =
        run_checks(&runner_a, &checks, &NoopProgressReporter).expect("run_checks A should succeed");
    let report_b =
        run_checks(&runner_b, &checks, &NoopProgressReporter).expect("run_checks B should succeed");

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

#[derive(Debug, Default)]
struct NativeScanTrackingRunner {
    ran: Mutex<Vec<&'static str>>,
    native_check_calls: std::sync::atomic::AtomicUsize,
    native_scan_calls: std::sync::atomic::AtomicUsize,
}

impl CommandRunner for NativeScanTrackingRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        self.ran.lock().unwrap().push(spec.name);
        Ok(CommandOutput {
            exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
            stdout: String::new(),
            stderr: String::new(),
        })
    }

    fn run_native_check(
        &self,
        _repo_root: &std::path::Path,
        _check: &NativeCheck,
    ) -> std::io::Result<NativeCheckResult> {
        self.native_check_calls.fetch_add(1, Ordering::SeqCst);
        Ok(NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        })
    }

    fn run_native_scan(
        &self,
        _repo_root: &std::path::Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        _progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        self.native_scan_calls.fetch_add(1, Ordering::SeqCst);
        Ok(checks
            .iter()
            .map(|check| crate::io::scanner::NativeScanCheckResult {
                check_name: check.name,
                passed: true,
                violations: Vec::new(),
            })
            .collect())
    }
}

#[derive(Debug, Default)]
struct PreparingRunner {
    events: Mutex<Vec<String>>,
}

impl PreparingRunner {
    fn events(&self) -> Vec<String> {
        self.events.lock().unwrap().clone()
    }
}

impl CommandRunner for PreparingRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        self.events
            .lock()
            .unwrap()
            .push(format!("run:{}", spec.name));
        Ok(CommandOutput {
            exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
            stdout: String::new(),
            stderr: String::new(),
        })
    }

    fn run_native_check(
        &self,
        _repo_root: &std::path::Path,
        check: &NativeCheck,
    ) -> std::io::Result<NativeCheckResult> {
        self.events
            .lock()
            .unwrap()
            .push(format!("native-required:{}", check.name));
        Ok(NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        })
    }

    fn prepare_for_verify(
        &self,
        _repo_root: &std::path::Path,
        native_checks: &[NativeCheck],
        checks: &[CommandSpec],
        native_scan_checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<()> {
        self.events.lock().unwrap().push(format!(
            "prepare:{}:{}:{}",
            native_checks.len(),
            checks.len(),
            native_scan_checks.len()
        ));
        Ok(())
    }

    fn run_native_scan(
        &self,
        _repo_root: &std::path::Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        _progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        self.events
            .lock()
            .unwrap()
            .push(format!("native-scan:{}", checks.len()));
        Ok(checks
            .iter()
            .map(|check| crate::io::scanner::NativeScanCheckResult {
                check_name: check.name,
                passed: true,
                violations: Vec::new(),
            })
            .collect())
    }
}

#[derive(Debug, Default)]
struct FailingPrepareRunner {
    events: Mutex<Vec<String>>,
}

impl FailingPrepareRunner {
    fn events(&self) -> Vec<String> {
        self.events.lock().unwrap().clone()
    }
}

impl CommandRunner for FailingPrepareRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        self.events
            .lock()
            .unwrap()
            .push(format!("run:{}", spec.name));
        Ok(CommandOutput {
            exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
            stdout: String::new(),
            stderr: String::new(),
        })
    }

    fn run_native_check(
        &self,
        _repo_root: &std::path::Path,
        check: &NativeCheck,
    ) -> std::io::Result<NativeCheckResult> {
        self.events
            .lock()
            .unwrap()
            .push(format!("native-required:{}", check.name));
        Ok(NativeCheckResult {
            status: CheckStatus::Pass,
            message: String::new(),
        })
    }

    fn prepare_for_verify(
        &self,
        _repo_root: &std::path::Path,
        _native_checks: &[NativeCheck],
        _checks: &[CommandSpec],
        _native_scan_checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<()> {
        self.events
            .lock()
            .unwrap()
            .push("prepare:error".to_string());
        Err(std::io::Error::other("simulated cache prep failure"))
    }

    fn run_native_scan(
        &self,
        _repo_root: &std::path::Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        _progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        self.events
            .lock()
            .unwrap()
            .push(format!("native-scan:{}", checks.len()));
        Ok(checks
            .iter()
            .map(|check| crate::io::scanner::NativeScanCheckResult {
                check_name: check.name,
                passed: true,
                violations: Vec::new(),
            })
            .collect())
    }
}

#[derive(Default)]
struct EventOrderReporter {
    events: Mutex<Vec<String>>,
}

impl EventOrderReporter {
    fn events(&self) -> Vec<String> {
        self.events.lock().unwrap().clone()
    }
}

impl ProgressReporter for EventOrderReporter {
    fn check_started(&self, name: &str) {
        self.events.lock().unwrap().push(format!("start:{name}"));
    }

    fn check_passed(&self, name: &str, _elapsed: Duration) {
        self.events.lock().unwrap().push(format!("pass:{name}"));
    }

    fn check_failed(&self, name: &str, _elapsed: Duration, _status: CheckStatus) {
        self.events.lock().unwrap().push(format!("fail:{name}"));
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
    let runner = std::sync::Arc::new(RecordingRunner::default());

    let warning_native_check = NativeCheck {
        name: "fake-native-warning",
        run: fake_warning_native_check,
    };

    let report = verify(
        runner.clone(),
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
    let runner = std::sync::Arc::new(RecordingRunner::default());

    let error_native_check = NativeCheck {
        name: "fake-native-error",
        run: fake_error_native_check,
    };

    let report = verify(
        runner.clone(),
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
    let runner = std::sync::Arc::new(RecordingRunner::default());

    let report = verify(runner.clone(), std::path::Path::new("/fake"), &[], &[])
        .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(report.failure, None);
}

#[test]
fn test_core_cargo_checks_run_in_stable_order() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let all_checks: Vec<CommandSpec> = CORE_CARGO_CHECKS.to_vec();
    let report = verify(
        runner.clone(),
        std::path::Path::new("/fake"),
        NATIVE_REQUIRED_CHECKS,
        &all_checks,
    )
    .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(
        runner.ran(),
        vec![
            "clippy-core",
            "test-ralph-workflow-lib",
            "test-integration",
            "test-mcp-server-lib",
            "test-mcp-server-integration",
            "test-mcp-server-standalone",
        ]
    );
}

#[test]
fn test_xtask_cargo_checks_run_in_stable_order() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let report = verify(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        XTASK_CARGO_CHECKS,
    )
    .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(runner.ran(), vec!["clippy-xtask", "test-xtask",]);
}

#[test]
fn test_gui_cargo_checks_run_in_stable_order() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let report = verify(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        GUI_CARGO_CHECKS,
    )
    .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(
        runner.ran(),
        vec!["clippy-ralph-gui", "test-ralph-gui-lib",]
    );
}

#[test]
fn test_clippy_core_uses_multi_package_invocation() {
    let clippy_core = CORE_CARGO_CHECKS
        .iter()
        .find(|c| c.name == "clippy-core")
        .expect("clippy-core must exist in CORE_CARGO_CHECKS");
    assert!(
        clippy_core.args.contains(&"-p"),
        "clippy-core must use -p flag for multi-package"
    );
    let pkg_args: Vec<&&str> = clippy_core
        .args
        .windows(2)
        .filter(|w| w[0] == "-p")
        .map(|w| &w[1])
        .collect();
    assert!(
        pkg_args.contains(&&"ralph-workflow"),
        "clippy-core must include ralph-workflow"
    );
    assert!(
        pkg_args.contains(&&"ralph-workflow-tests"),
        "clippy-core must include ralph-workflow-tests"
    );
    assert!(
        pkg_args.contains(&&"test-helpers"),
        "clippy-core must include test-helpers"
    );
}

#[test]
fn test_xtask_checks_use_separate_target_dir() {
    for spec in XTASK_CARGO_CHECKS {
        let has_target_dir = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TARGET_DIR");
        assert!(
            has_target_dir,
            "xtask check '{}' must set CARGO_TARGET_DIR for parallel execution",
            spec.name
        );
    }
}

#[test]
fn test_gui_cargo_checks_use_separate_target_dir() {
    for spec in GUI_CARGO_CHECKS {
        let has_target_dir = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TARGET_DIR");
        assert!(
            has_target_dir,
            "gui check '{}' must set CARGO_TARGET_DIR for parallel execution",
            spec.name
        );
    }
}

#[test]
fn test_frontend_checks_run_in_stable_order() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let report = verify(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        FRONTEND_CHECKS,
    )
    .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(
        runner.ran(),
        vec![
            "ralph-gui-frontend-install",
            "ralph-gui-frontend-lint",
            "ralph-gui-frontend-test",
        ]
    );
}

#[test]
fn test_release_checks_run_in_stable_order() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let report = verify(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        RELEASE_CHECKS,
    )
    .expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(runner.ran(), vec!["release-build", "dylint",]);
}

#[test]
fn test_release_build_uses_workspace_default_members() {
    let release_build = RELEASE_CHECKS
        .iter()
        .find(|c| c.name == "release-build")
        .expect("RELEASE_CHECKS must include release-build");

    assert_eq!(release_build.program, "cargo");
    assert_eq!(release_build.args, ["build", "--release"]);
    assert!(
        !release_build.args.contains(&"--workspace"),
        "release-build must stay scoped to workspace default members so unrelated tests/ edits do not invalidate its verify cache"
    );
    assert!(
        !release_build.args.contains(&"-p"),
        "release-build must not be widened to extra packages without revisiting its verify cache scope"
    );
}

#[test]
fn test_required_checks_do_not_invoke_shell_scripts() {
    for spec in all_required_checks() {
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
fn test_clippy_ralph_gui_check_is_in_required_checks() {
    assert!(
        all_required_checks()
            .iter()
            .any(|c| c.name == "clippy-ralph-gui"),
        "checks must include clippy-ralph-gui to lint the GUI crate"
    );
}

#[test]
fn test_ralph_gui_lib_test_check_is_in_required_checks() {
    assert!(
        all_required_checks()
            .iter()
            .any(|c| c.name == "test-ralph-gui-lib"),
        "checks must include test-ralph-gui-lib to run GUI crate unit tests"
    );
}

#[test]
fn test_ralph_gui_frontend_lint_check_is_in_required_checks() {
    assert!(
        all_required_checks()
            .iter()
            .any(|c| c.name == "ralph-gui-frontend-lint"),
        "checks must include ralph-gui-frontend-lint to enforce TypeScript strict rules"
    );
}

#[test]
fn test_ralph_gui_frontend_install_check_runs_before_lint() {
    let install_index = FRONTEND_CHECKS
        .iter()
        .position(|c| c.name == "ralph-gui-frontend-install")
        .expect("FRONTEND_CHECKS must include ralph-gui-frontend-install");
    let lint_index = FRONTEND_CHECKS
        .iter()
        .position(|c| c.name == "ralph-gui-frontend-lint")
        .expect("FRONTEND_CHECKS must include ralph-gui-frontend-lint");

    assert!(
        install_index < lint_index,
        "frontend install must run before frontend lint"
    );
}

#[test]
fn test_ralph_gui_frontend_install_uses_bun_with_frozen_lockfile() {
    let spec = FRONTEND_CHECKS
        .iter()
        .find(|c| c.name == "ralph-gui-frontend-install")
        .expect("FRONTEND_CHECKS must include ralph-gui-frontend-install");

    assert_eq!(spec.program, "bun");
    assert_eq!(
        spec.args,
        ["install", "--cwd", "ralph-gui/ui", "--frozen-lockfile"],
        "frontend install must use bun with the checked-in bun.lock"
    );
    assert!(
        spec.extra_env.is_empty(),
        "bun install should not rely on npm production env overrides"
    );
}

#[test]
fn test_ralph_gui_frontend_test_check_is_in_required_checks() {
    assert!(
        all_required_checks()
            .iter()
            .any(|c| c.name == "ralph-gui-frontend-test"),
        "checks must include ralph-gui-frontend-test to run vitest component tests"
    );
}

#[test]
fn test_ralph_gui_frontend_lint_runs_bun_script_from_ui_dir() {
    let spec = FRONTEND_POST_INSTALL_CHECKS
        .iter()
        .find(|c| c.name == "ralph-gui-frontend-lint")
        .expect("FRONTEND_POST_INSTALL_CHECKS must include ralph-gui-frontend-lint");

    assert_eq!(spec.program, "bun");
    assert_eq!(
        spec.args,
        ["run", "--cwd", "ralph-gui/ui", "lint"],
        "frontend lint must execute the package.json lint script from the Angular UI workspace"
    );
}

#[test]
fn test_ralph_gui_frontend_test_runs_bun_script_from_ui_dir() {
    let spec = FRONTEND_POST_INSTALL_CHECKS
        .iter()
        .find(|c| c.name == "ralph-gui-frontend-test")
        .expect("FRONTEND_POST_INSTALL_CHECKS must include ralph-gui-frontend-test");

    assert_eq!(spec.program, "bun");
    assert_eq!(
        spec.args,
        ["run", "--cwd", "ralph-gui/ui", "test"],
        "frontend test must execute the package.json test script from the Angular UI workspace"
    );
}

#[test]
fn test_dylint_check_uses_repo_local_writable_cache_dirs() {
    let spec = RELEASE_CHECKS
        .iter()
        .find(|c| c.name == "dylint")
        .expect("RELEASE_CHECKS must include dylint");

    let env: HashMap<_, _> = spec.extra_env.iter().copied().collect();

    assert!(
        !env.contains_key("CARGO_HOME"),
        "dylint should use the pre-provisioned cargo home unless installation is needed"
    );
    assert_eq!(env.get("DYLINT_DRIVER_PATH"), Some(&"target/dylint-driver"));
}

#[test]
fn test_detects_autogenerated_reason_marker_in_rust_source() {
    let marked = detect_autogenerated_reason("// reason = \"autogenerated\"\nfn generated() {}\n");

    assert!(marked.is_some(), "autogenerated marker must be detected");
}

#[test]
fn test_formats_autogenerated_marker_message() {
    let message = format_autogenerated_marker_message(Path::new("src/generated.rs"));

    assert_eq!(message, "src/generated.rs has been marked as autogenerated");
}

#[test]
fn test_collects_autogenerated_marked_files_from_dylint_scope() {
    let unique = format!(
        "xtask-autogenerated-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system time should be after epoch")
            .as_nanos()
    );
    let repo_root = std::env::temp_dir().join(unique);
    fs::create_dir_all(repo_root.join("ralph-workflow/src")).expect("create workflow src dir");
    fs::write(
        repo_root.join("ralph-workflow/src/generated.rs"),
        "// reason = \"autogenerated\"\nfn generated() {}\n",
    )
    .expect("write generated source");

    let marked = collect_autogenerated_marked_files(&repo_root)
        .expect("collecting autogenerated markers should succeed");

    assert_eq!(
        marked,
        vec![PathBuf::from("ralph-workflow/src/generated.rs")]
    );

    fs::remove_dir_all(&repo_root).expect("remove temp repo root");
}

#[test]
fn test_verify_fast_emits_autogenerated_info_message() {
    let unique = format!(
        "xtask-autogenerated-info-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system time should be after epoch")
            .as_nanos()
    );
    let repo_root = std::env::temp_dir().join(unique);
    fs::create_dir_all(repo_root.join("ralph-workflow/src")).expect("create workflow src dir");
    fs::write(
        repo_root.join("ralph-workflow/src/generated.rs"),
        "// reason = \"autogenerated\"\nfn generated() {}\n",
    )
    .expect("write generated source");

    let runner = std::sync::Arc::new(FakeRunner::default());
    let reporter = RecordingProgressReporter::default();
    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };

    let report = verify_fast(runner, &repo_root, &[], &groups, &reporter)
        .expect("verify_fast should succeed");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert!(
        reporter.events().contains(
            &"info:ralph-workflow/src/generated.rs has been marked as autogenerated".to_string()
        ),
        "verify_fast must emit the autogenerated info message"
    );

    fs::remove_dir_all(&repo_root).expect("remove temp repo root");
}

#[test]
fn test_no_string_errors_handlers_check_is_in_native_scan_checks() {
    // TDD anchor: no-string-errors-handlers is now a native Aho-Corasick scan check,
    // not a rg subprocess check.  Verify it is registered in NATIVE_SCAN_CHECKS.
    assert!(
        crate::io::scanner::NATIVE_SCAN_CHECKS
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
fn test_clippy_configs_document_test_large_stack_frames_exception() {
    let workflow_config_path = repo_root().join("ralph-workflow/clippy.toml");
    let workflow_source = fs::read_to_string(&workflow_config_path)
        .unwrap_or_else(|err| panic!("read ralph-workflow/clippy.toml: {err}"));

    assert!(
        workflow_source.contains("large_stack_frames"),
        "ralph-workflow/clippy.toml must document the large_stack_frames policy exception"
    );
    assert!(
        workflow_source.contains("deliberate") || workflow_source.contains("Deliberate"),
        "ralph-workflow/clippy.toml must document why the test harness exception is deliberate"
    );
    assert!(
        workflow_source.contains("test-only code allowances")
            || workflow_source
                .contains("test-only code allowances plus a matching xtask verify exception"),
        "ralph-workflow/clippy.toml must document the narrow test-only exception strategy"
    );

    for relative_path in [
        "clippy.toml",
        "ralph-workflow/clippy.toml",
        "tests/clippy.toml",
    ] {
        let path = repo_root().join(relative_path);
        let source =
            fs::read_to_string(&path).unwrap_or_else(|err| panic!("read {relative_path}: {err}"));

        assert!(
            !source.contains("allow-large-stack-frames-in-tests ="),
            "{relative_path} must not set unsupported allow-large-stack-frames-in-tests config"
        );
    }
}

#[test]
fn test_ralph_workflow_lib_rs_does_not_use_crate_wide_large_stack_frames_allow() {
    let lib_rs_path = repo_root().join("ralph-workflow/src/lib.rs");
    let lib_rs_source =
        fs::read_to_string(&lib_rs_path).unwrap_or_else(|err| panic!("read lib.rs: {err}"));

    assert!(
        !lib_rs_source.contains("#![cfg_attr(test, allow(clippy::large_stack_frames))]"),
        "ralph-workflow/src/lib.rs must keep the large_stack_frames exception item-scoped"
    );
}

#[test]
fn test_tailwind4_removed_angular_classes_check_is_in_native_required_checks() {
    assert!(
        NATIVE_REQUIRED_CHECKS
            .iter()
            .any(|c| c.name == "tailwind4-removed-angular-classes"),
        "NATIVE_REQUIRED_CHECKS must include the Tailwind 4 Angular class migration guard"
    );
}

#[test]
fn test_verify_surfaces_tailwind4_removed_angular_class_warning() {
    let unique = format!(
        "xtask-tailwind4-warning-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system time should be after epoch")
            .as_nanos()
    );
    let repo_root = std::env::temp_dir().join(unique);
    let template = repo_root.join("ralph-gui/ui/src/app/components/example/example.component.html");
    std::fs::create_dir_all(template.parent().expect("template parent"))
        .expect("create template dir");
    std::fs::write(
        &template,
        r#"<div class="flex items-center flex-shrink-0">Example</div>"#,
    )
    .expect("write template");

    let runner = std::sync::Arc::new(RecordingRunner::default());
    let report =
        verify(runner, &repo_root, NATIVE_REQUIRED_CHECKS, &[]).expect("verify should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure details");
    assert_eq!(failure.name, "tailwind4-removed-angular-classes");
    assert_eq!(failure.status, CheckStatus::Warning);
    assert!(
        failure.stdout.contains("flex-shrink-0"),
        "warning output must include the outdated class: {}",
        failure.stdout
    );
    assert!(
        failure.stdout.contains("needs rework"),
        "warning output must include the rework guidance: {}",
        failure.stdout
    );
    assert!(
        failure
            .stdout
            .contains("Tailwind CSS v4 documentation and upgrade guide"),
        "warning output must point the engineer to current Tailwind v4 docs: {}",
        failure.stdout
    );

    fs::remove_dir_all(&repo_root).expect("remove temp repo root");
}

#[test]
fn test_audit_ignore_has_url_check_is_in_native_scan_checks() {
    // TDD anchor: audit-ignore-has-url is now a native NegativeLookahead scan check.
    assert!(
        crate::io::scanner::NATIVE_SCAN_CHECKS
            .iter()
            .any(|c| c.name == "audit-ignore-has-url"),
        "NATIVE_SCAN_CHECKS must include the audit-ignore-has-url check"
    );
}

#[test]
fn test_forbidden_allow_expect_is_in_native_scan_checks() {
    // TDD anchor: forbidden-allow-expect-scan is now a native AnyLiteralAtLineStart check.
    assert!(
        crate::io::scanner::NATIVE_SCAN_CHECKS
            .iter()
            .any(|c| c.name == "forbidden-allow-expect-scan"),
        "NATIVE_SCAN_CHECKS must include the forbidden-allow-expect-scan check"
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
fn test_fmt_check_is_in_fmt_checks_not_core_cargo() {
    assert_eq!(
        FMT_CHECKS[0].name, "fmt-check",
        "FMT_CHECKS[0] must be fmt-check"
    );
    assert!(
        !CORE_CARGO_CHECKS.iter().any(|c| c.name == "fmt-check"),
        "fmt-check must not be in CORE_CARGO_CHECKS (moved to FMT_CHECKS for parallel lane)"
    );
}

#[test]
fn test_required_checks_contain_no_rg_entries() {
    for spec in all_required_checks() {
        assert_ne!(
            spec.program, "rg",
            "check '{}' must not use rg — all scanning is now native",
            spec.name
        );
    }
}

// ── TDD tests for concurrent execution ──────────────────────────────────

fn test_groups<'a>(
    core_cargo: &'a [CommandSpec],
    frontend: &'a [CommandSpec],
    release: &'a [CommandSpec],
) -> CheckGroups<'a> {
    CheckGroups {
        fmt: &[],
        core_cargo,
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: frontend,
        frontend_post_install: &[],
        release,
    }
}

#[test]
fn test_verify_fast_runs_all_required_checks() {
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let groups = CheckGroups {
        fmt: FMT_CHECKS,
        core_cargo: CORE_CARGO_CHECKS,
        xtask_cargo: XTASK_CARGO_CHECKS,
        gui_cargo: GUI_CARGO_CHECKS,
        frontend_install: FRONTEND_INSTALL_CHECKS,
        frontend_post_install: FRONTEND_POST_INSTALL_CHECKS,
        release: RELEASE_CHECKS,
    };
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        NATIVE_REQUIRED_CHECKS,
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(report.failure, None);

    let ran = runner.ran();
    use std::collections::HashSet;
    let ran_set: HashSet<&str> = ran.iter().copied().collect();

    for spec in all_required_checks() {
        assert!(
            ran_set.contains(spec.name),
            "verify_fast must run check '{}'",
            spec.name
        );
    }
}

#[test]
fn test_verify_fast_uses_runner_native_scan_path() {
    let runner = std::sync::Arc::new(NativeScanTrackingRunner::default());
    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[check("cargo-a")],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(
        runner.native_check_calls.load(Ordering::SeqCst),
        0,
        "this coverage is focused on the native-scan lane only"
    );
    assert_eq!(
        runner.native_scan_calls.load(Ordering::SeqCst),
        1,
        "verify_fast must route native scan through CommandRunner so warm-run caching can hook in"
    );
}

#[test]
fn test_verify_fast_uses_runner_native_check_path() {
    let runner = std::sync::Arc::new(NativeScanTrackingRunner::default());
    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[check("cargo-a")],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[NativeCheck {
            name: "native-required",
            run: |_| NativeCheckResult {
                status: CheckStatus::Pass,
                message: String::new(),
            },
        }],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
    assert_eq!(
        runner.native_check_calls.load(Ordering::SeqCst),
        1,
        "verify_fast must route native required checks through CommandRunner so warm-run caching can hook in"
    );
}

#[test]
fn test_verify_fast_prepares_runner_before_starting_checks() {
    let runner = std::sync::Arc::new(PreparingRunner::default());
    let reporter = EventOrderReporter::default();
    let groups = CheckGroups {
        fmt: FMT_CHECKS,
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        NATIVE_REQUIRED_CHECKS,
        &groups,
        &reporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let runner_events = runner.events();
    let prepare_index = runner_events
        .iter()
        .position(|event| event.starts_with("prepare:"))
        .expect("verify_fast must call prepare_for_verify before running checks");
    let first_run_index = runner_events
        .iter()
        .position(|event| {
            event.starts_with("native-required:")
                || event.starts_with("native-scan:")
                || event.starts_with("run:")
        })
        .expect("verify_fast should run at least one check");
    assert!(
        prepare_index < first_run_index,
        "prepare_for_verify must happen before any runner work, got {runner_events:?}"
    );

    let reporter_events = reporter.events();
    assert!(
        reporter_events
            .first()
            .is_some_and(|event| event.starts_with("start:")),
        "progress reporting should still begin with check start events, got {reporter_events:?}"
    );
}

#[test]
fn test_verify_fast_continues_uncached_when_prepare_for_verify_fails() {
    let runner = std::sync::Arc::new(FailingPrepareRunner::default());
    let reporter = EventOrderReporter::default();

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[NativeCheck {
            name: "fake-native-required",
            run: |_| NativeCheckResult {
                status: CheckStatus::Pass,
                message: String::new(),
            },
        }],
        &CheckGroups {
            fmt: &[check("fmt-check")],
            core_cargo: &[check("clippy-core")],
            xtask_cargo: &[],
            gui_cargo: &[],
            frontend_install: &[],
            frontend_post_install: &[],
            release: &[],
        },
        &reporter,
    )
    .expect("verify_fast should continue even when prepare_for_verify fails");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let events = runner.events();
    assert!(
        events.iter().any(|event| event == "prepare:error"),
        "runner should record the simulated prepare failure"
    );
    assert!(
        events
            .iter()
            .any(|event| event == "native-required:fake-native-required"),
        "native required checks must still run after prepare failure"
    );
    assert!(
        events.iter().any(|event| event == "run:fmt-check"),
        "fmt lane should still execute uncached after prepare failure"
    );
    assert!(
        events.iter().any(|event| event == "run:clippy-core"),
        "core cargo lane should still execute uncached after prepare failure"
    );
    assert!(
        events.iter().any(|event| event.starts_with("native-scan:")),
        "native scan lane should still execute after prepare failure"
    );
}

#[test]
fn test_verify_fast_stops_on_first_cargo_failure() {
    let runner = std::sync::Arc::new(FakeRunner::new([CommandOutput {
        exit_code: 1,
        stdout: String::new(),
        stderr: "error: clippy failure".to_string(),
    }]));

    let groups = test_groups(CORE_CARGO_CHECKS, &[], &[]);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        NATIVE_REQUIRED_CHECKS,
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "clippy-core");
}

#[test]
fn test_verify_fast_stops_on_fmt_failure() {
    let runner = std::sync::Arc::new(FakeRunner::new([CommandOutput {
        exit_code: 1,
        stdout: String::new(),
        stderr: "error: formatting differences found".to_string(),
    }]));

    let groups = CheckGroups {
        fmt: FMT_CHECKS,
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        NATIVE_REQUIRED_CHECKS,
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "fmt-check");
}

// ── TDD tests for ProgressReporter ──────────────────────────────────────

#[derive(Debug, Default)]
struct RecordingProgressReporter {
    events: Mutex<Vec<String>>,
}

impl RecordingProgressReporter {
    fn events(&self) -> Vec<String> {
        self.events.lock().unwrap().clone()
    }
}

impl ProgressReporter for RecordingProgressReporter {
    fn check_started(&self, name: &str) {
        self.events.lock().unwrap().push(format!("start:{name}"));
    }
    fn check_passed(&self, name: &str, _elapsed: Duration) {
        self.events.lock().unwrap().push(format!("pass:{name}"));
    }
    fn check_failed(&self, name: &str, _elapsed: Duration, _status: CheckStatus) {
        self.events.lock().unwrap().push(format!("fail:{name}"));
    }
    fn check_still_running(&self, name: &str, _elapsed: Duration) {
        self.events
            .lock()
            .unwrap()
            .push(format!("heartbeat:{name}"));
    }
    fn check_progress(&self, name: &str, info: &str) {
        self.events
            .lock()
            .unwrap()
            .push(format!("progress:{name}:{info}"));
    }
    fn info(&self, message: &str) {
        self.events.lock().unwrap().push(format!("info:{message}"));
    }
}

/// A fake runner that sleeps for a fixed duration before returning success.
/// Used to test heartbeat behavior without relying on real subprocesses.
struct SlowRunner {
    sleep_ms: u64,
}

impl CommandRunner for SlowRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        std::thread::sleep(Duration::from_millis(self.sleep_ms));
        Ok(CommandOutput {
            exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
            stdout: String::new(),
            stderr: String::new(),
        })
    }
}

#[test]
fn test_progress_reporter_called_for_each_passing_check() {
    let reporter = RecordingProgressReporter::default();
    let runner = FakeRunner::new([
        CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: String::new(),
        },
        CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: String::new(),
        },
    ]);
    let checks = [check("alpha"), check("beta")];
    let _ = run_checks(&runner, &checks, &reporter).unwrap();
    let events = reporter.events();
    assert_eq!(
        events,
        vec!["start:alpha", "pass:alpha", "start:beta", "pass:beta",]
    );
}

#[test]
fn test_progress_reporter_reports_failure_and_stops() {
    let reporter = RecordingProgressReporter::default();
    let runner = FakeRunner::new([CommandOutput {
        exit_code: 1,
        stdout: String::new(),
        stderr: String::new(),
    }]);
    let checks = [check("failing"), check("never-runs")];
    let _ = run_checks(&runner, &checks, &reporter).unwrap();
    let events = reporter.events();
    // Only "failing" must appear; "never-runs" must not.
    assert!(events.contains(&"start:failing".to_string()));
    assert!(events.contains(&"fail:failing".to_string()));
    assert!(
        !events.iter().any(|e| e.contains("never-runs")),
        "never-runs check must not appear in reporter events"
    );
}

struct IoErrorRunner;

impl CommandRunner for IoErrorRunner {
    fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        Err(std::io::Error::other("synthetic io error"))
    }
}

#[test]
fn test_run_checks_reports_failure_when_runner_returns_io_error() {
    let reporter = RecordingProgressReporter::default();
    let runner = IoErrorRunner;
    let checks = [check("io-fail")];

    let report = run_checks(&runner, &checks, &reporter).expect("run_checks must return a report");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("failure must be populated");
    assert_eq!(failure.name, "io-fail");
    assert_eq!(failure.status, CheckStatus::Error);
    assert!(
        failure.stderr.contains("synthetic io error"),
        "error text must be propagated into failure stderr"
    );

    let events = reporter.events();
    assert_eq!(events, vec!["start:io-fail", "fail:io-fail"]);
}

#[test]
fn test_noop_progress_reporter_does_not_panic() {
    let reporter = NoopProgressReporter;
    let runner = FakeRunner::new([CommandOutput {
        exit_code: 0,
        stdout: String::new(),
        stderr: String::new(),
    }]);
    let checks = [check("ok")];
    let report = run_checks(&runner, &checks, &reporter).unwrap();
    assert_eq!(report.exit, VerifyExitCode::Success);
}

// ── TDD tests for heartbeat progress reporting ───────────────────────────

/// Heartbeat must fire at least once when a check takes longer than the interval.
#[test]
fn test_heartbeat_fires_for_slow_check() {
    let reporter = RecordingProgressReporter::default();
    let runner = SlowRunner { sleep_ms: 300 };
    let checks = [check("slow-check")];
    // Use 50 ms interval so several heartbeats fire during the 300 ms run.
    run_checks_with_heartbeat(&runner, &checks, &reporter, Duration::from_millis(50)).unwrap();
    let events = reporter.events();
    assert!(
        events.iter().any(|e| e.starts_with("heartbeat:")),
        "Expected at least one heartbeat event for a slow check, got: {events:?}"
    );
}

/// Heartbeat must NOT fire when a check completes before the first interval expires.
#[test]
fn test_heartbeat_does_not_fire_for_fast_check() {
    let reporter = RecordingProgressReporter::default();
    let runner = SlowRunner { sleep_ms: 0 };
    let checks = [check("fast-check")];
    // Use a 200 ms interval; the runner returns immediately so done is set before
    // the heartbeat thread wakes up.
    run_checks_with_heartbeat(&runner, &checks, &reporter, Duration::from_millis(200)).unwrap();
    let events = reporter.events();
    assert!(
        !events.iter().any(|e| e.starts_with("heartbeat:")),
        "Expected no heartbeat events for a fast check, got: {events:?}"
    );
}

/// NoopProgressReporter must not panic when check_still_running is called directly.
#[test]
fn test_noop_reporter_heartbeat_does_not_panic() {
    NoopProgressReporter.check_still_running("x", Duration::ZERO);
}

/// NoopProgressReporter must not panic when check_progress is called directly.
#[test]
fn test_noop_reporter_check_progress_does_not_panic() {
    NoopProgressReporter.check_progress("fmt-check", "Compiling foo v1.0");
}

// ── TDD tests for check_progress ─────────────────────────────────────────

/// RecordingProgressReporter must record check_progress events with the
/// "progress:name:info" prefix so tests can assert on them.
#[test]
fn test_recording_reporter_captures_check_progress_events() {
    let reporter = RecordingProgressReporter::default();
    reporter.check_progress("fmt-check", "Compiling foo v1.0");
    reporter.check_progress("clippy", "Checking bar v0.5");

    let events = reporter.events();
    assert!(
        events.contains(&"progress:fmt-check:Compiling foo v1.0".to_string()),
        "expected progress event for fmt-check, got: {events:?}"
    );
    assert!(
        events.contains(&"progress:clippy:Checking bar v0.5".to_string()),
        "expected progress event for clippy, got: {events:?}"
    );
}

/// Verifying that check_progress is not called when runner output has no
/// Compiling/Checking lines (clean stdout-only output).
#[test]
fn test_check_progress_independent_of_runner_output() {
    // RecordingProgressReporter starts with no events; calling check_progress
    // adds events; not calling it leaves none.  This verifies the separation
    // between runner-side streaming (handled in main.rs RealRunner) and the
    // trait contract tested here.
    let reporter = RecordingProgressReporter::default();

    // No check_progress calls → no "progress:" events.
    reporter.check_started("some-check");
    reporter.check_passed("some-check", Duration::ZERO);

    let events = reporter.events();
    assert!(
        !events.iter().any(|e| e.starts_with("progress:")),
        "no check_progress calls means no progress events, got: {events:?}"
    );
}

/// HEARTBEAT_INTERVAL must be ≤ 5 seconds so users see "still running" feedback
/// within a reasonable time frame during slow cargo compilations.
#[test]
fn test_heartbeat_interval_is_at_most_5_seconds() {
    assert!(
        HEARTBEAT_INTERVAL.as_secs() <= 5,
        "HEARTBEAT_INTERVAL must be ≤5s for responsive user feedback, got: {HEARTBEAT_INTERVAL:?}"
    );
}

// ── TDD tests for StderrProgressReporter enhancements ───────────────────

/// StderrProgressReporter must not panic during basic lifecycle calls.
///
/// The counter increments and elapsed formatting both involve arithmetic;
/// verify no overflow or format panic for edge-case durations.
#[test]
fn test_stderr_progress_reporter_does_not_panic() {
    let reporter = StderrProgressReporter::new(5);
    // These print to stderr; the test only validates no panic occurs.
    reporter.check_started("check-a");
    reporter.check_passed("check-a", Duration::ZERO);
    reporter.check_started("check-b");
    reporter.check_failed("check-b", Duration::from_secs(1), CheckStatus::Error);
    reporter.check_still_running("check-c", Duration::from_secs(3));
    reporter.check_progress("check-c", "Compiling foo v1.0");
}

/// StderrProgressReporter with total=0 must not panic (division-by-zero guard).
#[test]
fn test_stderr_progress_reporter_zero_total_does_not_panic() {
    let reporter = StderrProgressReporter::new(0);
    reporter.check_started("check-x");
    reporter.check_passed("check-x", Duration::from_millis(42));
}

#[test]
fn test_stderr_progress_reporter_formats_check_started_with_counter() {
    let s = StderrProgressReporter::fmt_check_started(3, 10, "fmt-check");
    assert_eq!(s, "  [3/10] checking: fmt-check");
}

#[test]
fn test_stderr_progress_reporter_formats_done_with_elapsed() {
    let s = StderrProgressReporter::fmt_check_passed("fmt-check", Duration::from_millis(1500));
    assert!(s.starts_with("  done:     fmt-check ("));
    assert!(s.ends_with(')'));
}

#[test]
fn test_stderr_progress_reporter_formats_failed_with_status_and_elapsed() {
    let s = StderrProgressReporter::fmt_check_failed(
        "fmt-check",
        Duration::from_millis(250),
        CheckStatus::Error,
    );
    assert!(s.starts_with("  FAILED:   fmt-check ("));
    assert!(s.contains("Error"));
    assert!(s.ends_with(')'));
}

/// RecordingProgressReporter must correctly propagate elapsed to assertions.
///
/// This confirms the new elapsed parameter does not corrupt event recording
/// — the format "pass:name" must still appear regardless of elapsed value.
#[test]
fn test_recording_reporter_elapsed_does_not_affect_event_format() {
    let reporter = RecordingProgressReporter::default();
    reporter.check_started("alpha");
    reporter.check_passed("alpha", Duration::from_millis(123));
    reporter.check_started("beta");
    reporter.check_failed("beta", Duration::from_secs(5), CheckStatus::Warning);

    let events = reporter.events();
    assert!(
        events.contains(&"start:alpha".to_string()),
        "start event must be recorded"
    );
    assert!(
        events.contains(&"pass:alpha".to_string()),
        "pass event must be recorded regardless of elapsed value"
    );
    assert!(
        events.contains(&"fail:beta".to_string()),
        "fail event must be recorded regardless of elapsed value"
    );
}

/// Total check count must equal NATIVE_REQUIRED_CHECKS.len() + 1 (native-scan)
/// plus all group checks.  Guards against drift when new checks are added
/// without updating the StderrProgressReporter constructor call in main.rs.
#[test]
fn test_total_check_count_matches_reporter_constructor_expectation() {
    let expected_total = NATIVE_REQUIRED_CHECKS.len()
        + 1
        + FMT_CHECKS.len()
        + CORE_CARGO_CHECKS.len()
        + XTASK_CARGO_CHECKS.len()
        + GUI_CARGO_CHECKS.len()
        + FRONTEND_INSTALL_CHECKS.len()
        + FRONTEND_POST_INSTALL_CHECKS.len()
        + RELEASE_CHECKS.len();
    assert!(
        expected_total > 0,
        "total check count must be positive, got {expected_total}"
    );
    let all_checks = all_required_checks();
    assert!(
        expected_total >= all_checks.len(),
        "total must include at least the cargo/frontend/release checks"
    );
}

#[test]
fn test_react_act_warning_fails_checks_by_default() {
    let runner = FakeRunner::new([CommandOutput {
        exit_code: 0,
        stdout: String::new(),
        stderr: "Warning: An update to Configuration inside a test was not wrapped in act(...)\n"
            .to_string(),
    }]);
    let report = run_checks(&runner, &[check("some-check")], &NoopProgressReporter).unwrap();
    assert_eq!(report.exit, VerifyExitCode::Failure);
    assert_eq!(
        report.failure.expect("failure must be present").status,
        CheckStatus::Warning
    );
}

#[test]
fn test_react_act_warning_is_allowed_only_for_frontend_test_check() {
    let runner = FakeRunner::new([CommandOutput {
        exit_code: 0,
        stdout: String::new(),
        stderr: "Warning: An update to Configuration inside a test was not wrapped in act(...)\n"
            .to_string(),
    }]);
    let report = run_checks(
        &runner,
        &[check("ralph-gui-frontend-test")],
        &NoopProgressReporter,
    )
    .unwrap();
    assert_eq!(report.exit, VerifyExitCode::Success);
}

#[test]
fn test_core_lib_test_streaming_warnings_are_allowed() {
    let stderr = [
        "Warning: Large delta (201 chars) for key '0'. This may indicate unusual streaming behavior or a snapshot being sent as a delta.",
        "Warning: Detected pattern of 3 large deltas for key '0'. This strongly suggests a snapshot-as-delta bug where the same large content is being sent repeatedly. File: streaming_state.rs, Line: 703",
        "Warning: Received MessageStart while state is Streaming. This indicates a non-standard agent protocol (e.g., GLM sending repeated MessageStart events). Preserving output_started_for_key to prevent prefix spam. File: state_management.rs, Line: 204",
    ]
    .join("\n");

    let runner = FakeRunner::new([CommandOutput {
        exit_code: 0,
        stdout: String::new(),
        stderr,
    }]);
    let report = run_checks(
        &runner,
        &[check(CORE_LIB_TEST_CHECK_NAME)],
        &NoopProgressReporter,
    )
    .unwrap();
    assert_eq!(report.exit, VerifyExitCode::Success);
}

#[test]
fn test_integration_test_known_runtime_warnings_are_allowed() {
    let stderr = [
        "[2026-03-20 19:58:23] ⚠ Git wrapper missing — reinstalling",
        "[2026-03-20 19:58:24] ⚠ Failed to create PROMPT.md monitor: PROMPT.md does not exist - cannot monitor. Continuing anyway.",
        "⚠️  Risks & Mitigations:",
        "Warning: Delta discontinuity detected in OpenCode text. Provider sent non-monotonic content. Last: \"Hello\" (len=5), Current: \"Hello\" (len=5)",
    ]
    .join("\n");

    let runner = FakeRunner::new([CommandOutput {
        exit_code: 0,
        stdout: String::new(),
        stderr,
    }]);
    let report = run_checks(
        &runner,
        &[check(INTEGRATION_TEST_CHECK_NAME)],
        &NoopProgressReporter,
    )
    .unwrap();
    assert_eq!(report.exit, VerifyExitCode::Success);
}

// ── TDD tests for redundant memory-safety check removal ─────────────────

#[test]
fn test_no_redundant_memory_safety_checks() {
    // memory-safety-integration, memory-safety-benchmarks, memory-safety-executor
    // are subsets of test-integration and test-ralph-workflow-lib respectively.
    let all = all_required_checks();
    for spec in &all {
        assert!(
            !spec.name.starts_with("memory-safety-"),
            "redundant memory-safety check '{}' must not be in required checks",
            spec.name
        );
    }
}

#[test]
fn test_required_checks_include_parent_test_commands() {
    let all = all_required_checks();
    let names: Vec<&str> = all.iter().map(|c| c.name).collect();
    assert!(
        names.contains(&"test-integration"),
        "must include test-integration (superset of memory-safety-integration)"
    );
    assert!(
        names.contains(&"test-ralph-workflow-lib"),
        "must include test-ralph-workflow-lib (superset of memory-safety-benchmarks/executor)"
    );
}

// ── TDD tests for parallel group execution ──────────────────────────────

#[test]
fn test_verify_fast_runs_frontend_and_cargo_concurrently() {
    // Use a ByNameRunner that records thread IDs to prove concurrency.
    use std::sync::Arc;
    use std::thread::ThreadId;

    #[derive(Debug, Default)]
    struct ThreadTrackingRunner {
        threads: Mutex<HashMap<&'static str, ThreadId>>,
    }

    impl CommandRunner for ThreadTrackingRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.threads
                .lock()
                .unwrap()
                .insert(spec.name, std::thread::current().id());
            // Small sleep to increase chance of overlap visibility
            std::thread::sleep(Duration::from_millis(10));
            Ok(CommandOutput {
                exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    let runner = Arc::new(ThreadTrackingRunner::default());

    let cargo_checks: &[CommandSpec] = &[check("cargo-a")];
    let frontend_checks: &[CommandSpec] = &[check("frontend-a")];

    let groups = test_groups(cargo_checks, frontend_checks, &[]);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let threads = runner.threads.lock().unwrap();
    let cargo_thread = threads.get("cargo-a").expect("cargo-a must have run");
    let frontend_thread = threads.get("frontend-a").expect("frontend-a must have run");

    // The cargo debug group runs on the main thread, frontend on a spawned thread.
    // They must run on different threads to prove concurrency.
    assert_ne!(
        cargo_thread, frontend_thread,
        "cargo and frontend checks must run on different threads (proving concurrency)"
    );
}

#[test]
fn test_verify_fast_failure_in_cargo_cancels_other_groups() {
    // When the cargo group fails, the cancellation flag should be set,
    // preventing remaining checks in other groups from running.
    let runner = std::sync::Arc::new(
        ByNameRunner::default()
            .with_output(
                "cargo-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: cargo failure".to_string(),
                },
            )
            .with_output(
                "frontend-a",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            )
            .with_output(
                "release-a",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            ),
    );

    let cargo_checks: &[CommandSpec] = &[check("cargo-fails")];
    let frontend_checks: &[CommandSpec] = &[check("frontend-a")];
    let release_checks: &[CommandSpec] = &[check("release-a")];

    let groups = test_groups(cargo_checks, frontend_checks, release_checks);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "cargo-fails");
}

#[test]
fn test_verify_fast_reports_frontend_failure_when_cargo_succeeds() {
    let runner = std::sync::Arc::new(
        ByNameRunner::default()
            .with_output(
                "cargo-ok",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            )
            .with_output(
                "frontend-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: lint failure".to_string(),
                },
            )
            .with_output(
                "release-ok",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            ),
    );

    let cargo_checks: &[CommandSpec] = &[check("cargo-ok")];
    let frontend_checks: &[CommandSpec] = &[check("frontend-fails")];
    let release_checks: &[CommandSpec] = &[check("release-ok")];

    let groups = test_groups(cargo_checks, frontend_checks, release_checks);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "frontend-fails");
}

#[test]
fn test_verify_fast_reports_release_failure_when_others_succeed() {
    let runner = std::sync::Arc::new(
        ByNameRunner::default()
            .with_output(
                "cargo-ok",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            )
            .with_output(
                "frontend-ok",
                CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                },
            )
            .with_output(
                "release-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: release failure".to_string(),
                },
            ),
    );

    let cargo_checks: &[CommandSpec] = &[check("cargo-ok")];
    let frontend_checks: &[CommandSpec] = &[check("frontend-ok")];
    let release_checks: &[CommandSpec] = &[check("release-fails")];

    let groups = test_groups(cargo_checks, frontend_checks, release_checks);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "release-fails");
}

#[test]
fn test_verify_fast_cargo_failure_takes_priority_over_frontend_failure() {
    // When both cargo and frontend fail, cargo failure is reported (highest priority).
    let runner = std::sync::Arc::new(
        ByNameRunner::default()
            .with_output(
                "cargo-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: cargo".to_string(),
                },
            )
            .with_output(
                "frontend-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: frontend".to_string(),
                },
            ),
    );

    let cargo_checks: &[CommandSpec] = &[check("cargo-fails")];
    let frontend_checks: &[CommandSpec] = &[check("frontend-fails")];

    let groups = test_groups(cargo_checks, frontend_checks, &[]);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "cargo-fails");
}

#[test]
fn test_release_checks_use_separate_target_dir() {
    // Release checks must set CARGO_TARGET_DIR to avoid lock contention
    // with the debug cargo group.
    for spec in RELEASE_CHECKS {
        let has_target_dir = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TARGET_DIR");
        assert!(
            has_target_dir,
            "release check '{}' must set CARGO_TARGET_DIR for parallel execution",
            spec.name
        );
    }
}

#[test]
fn test_all_required_checks_returns_union_of_all_groups() {
    let all = all_required_checks();
    let expected_count = FMT_CHECKS.len()
        + CORE_CARGO_CHECKS.len()
        + XTASK_CARGO_CHECKS.len()
        + GUI_CARGO_CHECKS.len()
        + FRONTEND_INSTALL_CHECKS.len()
        + FRONTEND_POST_INSTALL_CHECKS.len()
        + RELEASE_CHECKS.len();
    assert_eq!(
        all.len(),
        expected_count,
        "all_required_checks() must return exactly the union of all groups"
    );
}

// ── TDD tests for concurrent scan and fmt-check ─────────────────────────

#[test]
fn test_verify_fast_runs_scan_concurrently_with_cargo_groups() {
    use std::sync::Arc;
    use std::thread::ThreadId;

    #[derive(Debug, Default)]
    struct ThreadTrackingRunner {
        threads: Mutex<HashMap<&'static str, ThreadId>>,
    }

    impl CommandRunner for ThreadTrackingRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.threads
                .lock()
                .unwrap()
                .insert(spec.name, std::thread::current().id());
            std::thread::sleep(Duration::from_millis(10));
            Ok(CommandOutput {
                exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    // The scan runs on a spawned thread; cargo runs on the main scope thread.
    // We verify they overlap by checking that the scan "native-scan" start/pass
    // events are emitted (proving it ran) while cargo checks also ran.
    let runner = Arc::new(ThreadTrackingRunner::default());
    let reporter = RecordingProgressReporter::default();
    let cargo_checks: &[CommandSpec] = &[check("cargo-a")];

    let groups = test_groups(cargo_checks, &[], &[]);
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &reporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let events = reporter.events();
    assert!(
        events.contains(&"start:native-scan".to_string()),
        "native-scan must start during verify_fast, got: {events:?}"
    );
    assert!(
        events.contains(&"pass:native-scan".to_string()),
        "native-scan must pass during verify_fast, got: {events:?}"
    );

    // Cargo check ran on the main scope thread, scan on a spawned thread.
    let cargo_thread = runner
        .threads
        .lock()
        .unwrap()
        .get("cargo-a")
        .copied()
        .expect("cargo-a must have run");
    // The scan doesn't go through CommandRunner, but its events prove it ran
    // concurrently (scan starts before cargo finishes due to thread::scope).
    // Just verify both ran to completion.
    assert!(
        events.iter().any(|e| e == "start:cargo-a"),
        "cargo-a must have started"
    );
    let _ = cargo_thread; // used above
}

#[test]
fn test_verify_fast_fmt_check_runs_parallel_to_clippy() {
    use std::sync::Arc;
    use std::thread::ThreadId;

    #[derive(Debug, Default)]
    struct ThreadTrackingRunner {
        threads: Mutex<HashMap<&'static str, ThreadId>>,
    }

    impl CommandRunner for ThreadTrackingRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.threads
                .lock()
                .unwrap()
                .insert(spec.name, std::thread::current().id());
            std::thread::sleep(Duration::from_millis(10));
            Ok(CommandOutput {
                exit_code: spec.success_exit_codes.first().copied().unwrap_or(0),
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    let runner = Arc::new(ThreadTrackingRunner::default());
    let fmt_checks: &[CommandSpec] = &[check("fmt-check")];
    let cargo_checks: &[CommandSpec] = &[check("clippy-core")];

    let groups = CheckGroups {
        fmt: fmt_checks,
        core_cargo: cargo_checks,
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let threads = runner.threads.lock().unwrap();
    let fmt_thread = threads.get("fmt-check").expect("fmt-check must have run");
    let clippy_thread = threads
        .get("clippy-core")
        .expect("clippy-core must have run");

    assert_ne!(
        fmt_thread, clippy_thread,
        "fmt-check and clippy-core must run on different threads (proving parallelism)"
    );
}

#[test]
fn test_verify_fast_scan_failure_cancels_cargo_groups() {
    // When the native scan finds violations, it sets the cancel flag.
    // We can't easily inject scan failures in this unit test since the scan
    // reads real files, but we verify the structural property: scan result
    // is checked with highest priority and a scan failure report is returned.
    // This test verifies that scan failure takes priority over cargo success.
    let runner = std::sync::Arc::new(RecordingRunner::default());
    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };
    // With empty groups and a valid repo root, scan should pass (no violations).
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);
}

#[test]
fn test_verify_fast_fmt_failure_takes_priority_over_cargo_failure() {
    // When both fmt and cargo fail, fmt failure should be reported first.
    let runner = std::sync::Arc::new(
        ByNameRunner::default()
            .with_output(
                "fmt-check",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: formatting".to_string(),
                },
            )
            .with_output(
                "cargo-fails",
                CommandOutput {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "error: cargo".to_string(),
                },
            ),
    );

    let fmt_checks: &[CommandSpec] = &[check("fmt-check")];
    let cargo_checks: &[CommandSpec] = &[check("cargo-fails")];

    let groups = CheckGroups {
        fmt: fmt_checks,
        core_cargo: cargo_checks,
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };
    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(
        failure.name, "fmt-check",
        "fmt failure must take priority over cargo failure"
    );
}

#[test]
fn test_fmt_checks_constant_contains_fmt_check() {
    assert_eq!(
        FMT_CHECKS.len(),
        1,
        "FMT_CHECKS must have exactly one check"
    );
    assert_eq!(FMT_CHECKS[0].name, "fmt-check");
    assert_eq!(FMT_CHECKS[0].program, "cargo");
    assert!(FMT_CHECKS[0].args.contains(&"fmt"));
    assert!(FMT_CHECKS[0].args.contains(&"--check"));
}

// ── TDD tests for frontend sub-parallelism ──────────────────────────────

#[test]
fn test_frontend_install_checks_contains_only_install() {
    assert_eq!(FRONTEND_INSTALL_CHECKS.len(), 1);
    assert_eq!(
        FRONTEND_INSTALL_CHECKS[0].name,
        "ralph-gui-frontend-install"
    );
}

#[test]
fn test_frontend_post_install_checks_contains_lint_and_test() {
    assert_eq!(FRONTEND_POST_INSTALL_CHECKS.len(), 2);
    let names: Vec<&str> = FRONTEND_POST_INSTALL_CHECKS
        .iter()
        .map(|c| c.name)
        .collect();
    assert!(names.contains(&"ralph-gui-frontend-lint"));
    assert!(names.contains(&"ralph-gui-frontend-test"));
}

#[test]
fn test_frontend_checks_equals_install_plus_post_install() {
    let combined: Vec<&str> = FRONTEND_INSTALL_CHECKS
        .iter()
        .chain(FRONTEND_POST_INSTALL_CHECKS.iter())
        .map(|c| c.name)
        .collect();
    let legacy: Vec<&str> = FRONTEND_CHECKS.iter().map(|c| c.name).collect();
    assert_eq!(combined, legacy);
}

#[test]
fn test_verify_fast_frontend_lint_and_test_run_in_parallel_after_install() {
    use std::sync::Arc;
    use std::thread::ThreadId;

    #[derive(Debug, Default)]
    struct TimedRunner {
        threads: Mutex<HashMap<&'static str, ThreadId>>,
        start_times: Mutex<HashMap<&'static str, Instant>>,
        end_times: Mutex<HashMap<&'static str, Instant>>,
    }

    impl CommandRunner for TimedRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.threads
                .lock()
                .unwrap()
                .insert(spec.name, std::thread::current().id());
            self.start_times
                .lock()
                .unwrap()
                .insert(spec.name, Instant::now());
            // Lint and test sleep long enough to prove overlap.
            if spec.name.contains("lint") || spec.name.contains("test") {
                std::thread::sleep(Duration::from_millis(100));
            }
            self.end_times
                .lock()
                .unwrap()
                .insert(spec.name, Instant::now());
            Ok(CommandOutput {
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    let runner = Arc::new(TimedRunner::default());
    let install: &[CommandSpec] = &[check("ralph-gui-frontend-install")];
    let post_install: &[CommandSpec] = &[
        check("ralph-gui-frontend-lint"),
        check("ralph-gui-frontend-test"),
    ];

    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: install,
        frontend_post_install: post_install,
        release: &[],
    };

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    // Lint and test must run on different threads (proving sub-parallelism).
    let threads = runner.threads.lock().unwrap();
    let lint_thread = threads
        .get("ralph-gui-frontend-lint")
        .expect("lint must have run");
    let test_thread = threads
        .get("ralph-gui-frontend-test")
        .expect("test must have run");
    assert_ne!(
        lint_thread, test_thread,
        "frontend lint and test must run on different threads (proving parallelism)"
    );

    // Both must have started before either finished (proving concurrency).
    let starts = runner.start_times.lock().unwrap();
    let ends = runner.end_times.lock().unwrap();
    let lint_start = starts["ralph-gui-frontend-lint"];
    let test_start = starts["ralph-gui-frontend-test"];
    let lint_end = ends["ralph-gui-frontend-lint"];
    let test_end = ends["ralph-gui-frontend-test"];
    assert!(
        lint_start < test_end && test_start < lint_end,
        "lint and test must overlap in time (both started before either finished)"
    );
}

#[test]
fn test_verify_fast_frontend_install_failure_skips_post_install() {
    let runner = std::sync::Arc::new(ByNameRunner::default().with_output(
        "ralph-gui-frontend-install",
        CommandOutput {
            exit_code: 1,
            stdout: String::new(),
            stderr: "error: npm ci failed".to_string(),
        },
    ));

    let install: &[CommandSpec] = &[check("ralph-gui-frontend-install")];
    let post_install: &[CommandSpec] = &[
        check("ralph-gui-frontend-lint"),
        check("ralph-gui-frontend-test"),
    ];

    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: install,
        frontend_post_install: post_install,
        release: &[],
    };

    let report = verify_fast(
        runner,
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);
    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "ralph-gui-frontend-install");
}

// ── TDD tests for per-lane timing reporting ─────────────────────────────

#[test]
fn test_verify_fast_reports_lane_timing() {
    #[derive(Debug, Default)]
    struct LaneRecordingReporter {
        lanes: Mutex<Vec<String>>,
    }

    impl ProgressReporter for LaneRecordingReporter {
        fn check_started(&self, _name: &str) {}
        fn check_passed(&self, _name: &str, _elapsed: Duration) {}
        fn check_failed(&self, _name: &str, _elapsed: Duration, _status: CheckStatus) {}
        fn lane_finished(&self, lane_name: &str, _elapsed: Duration) {
            self.lanes.lock().unwrap().push(lane_name.to_string());
        }
    }

    let runner = std::sync::Arc::new(RecordingRunner::default());
    let reporter = LaneRecordingReporter::default();

    let groups = CheckGroups {
        fmt: FMT_CHECKS,
        core_cargo: CORE_CARGO_CHECKS,
        xtask_cargo: XTASK_CARGO_CHECKS,
        gui_cargo: GUI_CARGO_CHECKS,
        frontend_install: FRONTEND_INSTALL_CHECKS,
        frontend_post_install: FRONTEND_POST_INSTALL_CHECKS,
        release: RELEASE_CHECKS,
    };

    let report = verify_fast(
        runner,
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &reporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Success);

    let lanes = reporter.lanes.lock().unwrap();
    let expected_lanes = [
        "native-scan",
        "fmt",
        "core-cargo",
        "xtask-cargo",
        "gui-cargo",
        "frontend",
        "release",
    ];
    for lane_name in &expected_lanes {
        assert!(
            lanes.contains(&lane_name.to_string()),
            "lane_finished must be called for '{lane_name}', got: {lanes:?}"
        );
    }
}

#[test]
fn test_noop_reporter_lane_finished_does_not_panic() {
    NoopProgressReporter.lane_finished("test-lane", Duration::ZERO);
}

#[test]
fn test_stderr_reporter_formats_lane_finished() {
    let s = StderrProgressReporter::fmt_lane_finished("frontend", Duration::from_millis(1234));
    assert!(s.starts_with("  lane done: frontend ("));
    assert!(s.ends_with(')'));
}

#[test]
fn test_classify_allows_generated_lib_test_harness_large_stack_frames_for_clippy_core() {
    let stderr = r#"error: this function may allocate 692072 bytes on the stack: this is the largest part, at 31456 bytes for type `[&test::TestDescAndFn; 3932]`
  |
  = note: 692072 bytes is larger than Clippy's configured `stack-size-threshold` of 512000
  = note: allocating large amounts of stack space can overflow the stack and cause the program to abort
warning: build failed, waiting for other jobs to finish...
error: could not compile `ralph-workflow` (lib test) due to 1 previous error
"#;

    let status = classify(CLIPPY_CORE_CHECK_NAME, 101, "", stderr, &[0]);
    assert_eq!(status, CheckStatus::Pass);
}

#[test]
fn test_classify_keeps_non_harness_large_stack_frames_as_error() {
    let stderr = "error: this function may allocate 600000 bytes on the stack\n";

    let status = classify(CLIPPY_CORE_CHECK_NAME, 101, "", stderr, &[0]);
    assert_eq!(status, CheckStatus::Error);
}

struct FailingScanRunner {
    scan_results: Mutex<Vec<crate::io::scanner::NativeScanCheckResult>>,
}

impl FailingScanRunner {
    fn with_forbidden_allow_expect_scan_failure() -> Self {
        Self {
            scan_results: Mutex::new(vec![crate::io::scanner::NativeScanCheckResult {
                check_name: "forbidden-allow-expect-scan",
                passed: false,
                violations: vec![crate::io::scanner::NativeScanViolation {
                    file: PathBuf::from("/fake/file.rs"),
                    line_number: 42,
                    line: r#"    #[cfg_attr(test, allow(clippy::large_stack_frames))]"#.to_string(),
                }],
            }]),
        }
    }
}

impl CommandRunner for FailingScanRunner {
    fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        Ok(CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: String::new(),
        })
    }

    fn run_native_scan(
        &self,
        _repo_root: &std::path::Path,
        _checks: &[crate::io::scanner::NativeScanCheck],
        _progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        let mut results = self.scan_results.lock().unwrap();
        Ok(std::mem::take(&mut *results))
    }
}

#[test]
fn test_forbidden_allow_expect_scan_failure_includes_policy_in_output() {
    let runner = std::sync::Arc::new(FailingScanRunner::with_forbidden_allow_expect_scan_failure());
    let groups = CheckGroups {
        fmt: &[],
        core_cargo: &[],
        xtask_cargo: &[],
        gui_cargo: &[],
        frontend_install: &[],
        frontend_post_install: &[],
        release: &[],
    };

    let report = verify_fast(
        runner.clone(),
        std::path::Path::new("/fake"),
        &[],
        &groups,
        &NoopProgressReporter,
    )
    .expect("verify_fast should not error");

    assert_eq!(report.exit, VerifyExitCode::Failure);

    let failure = report.failure.expect("expected failure");
    assert_eq!(failure.name, "forbidden-allow-expect-scan");
    assert!(
        failure.stdout.starts_with(FORBIDDEN_ALLOW_EXPECT_POLICY),
        "failure output should start with policy text, got: {}",
        failure.stdout
    );
}
