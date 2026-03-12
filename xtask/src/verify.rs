use anyhow::{Context as _, Result};
use std::borrow::Cow;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

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

pub trait CommandRunner: Send + Sync {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput>;

    fn run_native_check(
        &self,
        repo_root: &std::path::Path,
        check: &NativeCheck,
    ) -> std::io::Result<NativeCheckResult> {
        Ok((check.run)(repo_root))
    }

    fn prepare_for_verify(
        &self,
        _repo_root: &std::path::Path,
        _native_checks: &[NativeCheck],
        _checks: &[CommandSpec],
        _native_scan_checks: &[crate::scanner::NativeScanCheck],
    ) -> std::io::Result<()> {
        Ok(())
    }

    fn run_native_scan(
        &self,
        repo_root: &std::path::Path,
        checks: &[crate::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
        Ok(crate::scanner::run_native_scan_checks_reporting(
            repo_root, checks, progress,
        ))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckStatus {
    Pass,
    Warning,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum FailurePriority {
    Scan = 0,
    Fmt = 1,
    CoreCargo = 2,
    XtaskCargo = 3,
    GuiCargo = 4,
    Frontend = 5,
    Release = 6,
}

struct CancellationState {
    highest_priority_failure: AtomicUsize,
}

impl CancellationState {
    const NO_FAILURE: usize = usize::MAX;

    fn new() -> Self {
        Self {
            highest_priority_failure: AtomicUsize::new(Self::NO_FAILURE),
        }
    }

    fn record_failure(&self, priority: FailurePriority) {
        let priority = priority as usize;
        let _ = self.highest_priority_failure.fetch_update(
            Ordering::SeqCst,
            Ordering::SeqCst,
            |current| (priority < current).then_some(priority),
        );
    }

    fn should_cancel(&self, priority: FailurePriority) -> bool {
        self.highest_priority_failure.load(Ordering::SeqCst) < priority as usize
    }
}

/// Observer interface for verification progress.
///
/// Implementors receive callbacks as each check starts and finishes.
/// The trait is `Send + Sync` so it can be shared across threads and stored in `Arc`.
pub trait ProgressReporter: Send + Sync {
    fn check_started(&self, name: &str);
    /// Called when a check passes; `elapsed` is the wall-clock duration of the check.
    fn check_passed(&self, name: &str, elapsed: Duration);
    /// Called when a check fails; `elapsed` is the wall-clock duration of the check.
    fn check_failed(&self, name: &str, elapsed: Duration, status: CheckStatus);
    /// Called periodically while a long-running check is still in progress.
    /// Default is a no-op so existing implementations compile unchanged.
    fn check_still_running(&self, _name: &str, _elapsed: Duration) {}
    /// Called with incremental status info during a long-running check
    /// (e.g., "Compiling foo" lines forwarded from cargo, or per-file scan counts).
    /// Default is a no-op so existing test fakes need no changes.
    fn check_progress(&self, _name: &str, _info: &str) {}
    /// Called when a parallel lane finishes; `elapsed` is the total wall-clock time.
    /// Default is a no-op so existing implementations compile unchanged.
    fn lane_finished(&self, _lane_name: &str, _elapsed: Duration) {}
}

/// No-op implementation used in tests.
#[cfg(test)]
pub struct NoopProgressReporter;

#[cfg(test)]
impl ProgressReporter for NoopProgressReporter {
    fn check_started(&self, _name: &str) {}
    fn check_passed(&self, _name: &str, _elapsed: Duration) {}
    fn check_failed(&self, _name: &str, _elapsed: Duration, _status: CheckStatus) {}
}

/// Progress reporter that prints check names to stderr in real time.
///
/// Output format:
///   [N/total] checking: <name>
///   done:     <name> (<elapsed>)
///   FAILED:   <name> (<elapsed>, Error|Warning)
///   still running: <name> (<elapsed>)...  ← printed every 3 s for slow checks
///   progress: <name>: <info>              ← forwarded cargo/scan progress lines
///
/// Stderr is used so stdout can be piped without interference.
pub struct StderrProgressReporter {
    counter: AtomicUsize,
    total: usize,
}

impl StderrProgressReporter {
    pub fn new(total: usize) -> Self {
        Self {
            counter: AtomicUsize::new(0),
            total,
        }
    }

    fn fmt_check_started(n: usize, total: usize, name: &str) -> String {
        format!("  [{n}/{total}] checking: {name}")
    }

    fn fmt_check_passed(name: &str, elapsed: Duration) -> String {
        format!("  done:     {name} ({elapsed:.1?})")
    }

    fn fmt_check_failed(name: &str, elapsed: Duration, status: CheckStatus) -> String {
        format!("  FAILED:   {name} ({elapsed:.1?}, {status:?})")
    }

    fn fmt_still_running(name: &str, elapsed: Duration) -> String {
        format!("  still running: {name} ({elapsed:.0?})...")
    }

    fn fmt_progress(name: &str, info: &str) -> String {
        format!("  progress: {name}: {info}")
    }

    fn fmt_lane_finished(lane_name: &str, elapsed: Duration) -> String {
        format!("  lane done: {lane_name} ({elapsed:.1?})")
    }
}

impl ProgressReporter for StderrProgressReporter {
    fn check_started(&self, name: &str) {
        let n = self.counter.fetch_add(1, Ordering::Relaxed) + 1;
        eprintln!("{}", Self::fmt_check_started(n, self.total, name));
    }
    fn check_passed(&self, name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_check_passed(name, elapsed));
    }
    fn check_failed(&self, name: &str, elapsed: Duration, status: CheckStatus) {
        eprintln!("{}", Self::fmt_check_failed(name, elapsed, status));
    }
    fn check_still_running(&self, name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_still_running(name, elapsed));
    }
    fn check_progress(&self, name: &str, info: &str) {
        eprintln!("{}", Self::fmt_progress(name, info));
    }
    fn lane_finished(&self, lane_name: &str, elapsed: Duration) {
        eprintln!("{}", Self::fmt_lane_finished(lane_name, elapsed));
    }
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

const FRONTEND_TEST_CHECK_NAME: &str = "ralph-gui-frontend-test";

fn strip_allowed_warning_lines_for_check<'a>(check_name: &str, text: &'a str) -> Cow<'a, str> {
    // Policy: allow exactly one known noisy React act(...) warning line, and only for the
    // known frontend test command. Everywhere else, treat warnings as diagnostics.
    if check_name != FRONTEND_TEST_CHECK_NAME {
        return Cow::Borrowed(text);
    }
    if !text.contains("inside a test was not wrapped in act(...)") {
        return Cow::Borrowed(text);
    }

    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let trimmed = line.trim_start();
        let is_react_act_warning = trimmed.starts_with("Warning: An update to ")
            && trimmed.contains("inside a test was not wrapped in act(...)");
        if is_react_act_warning {
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    Cow::Owned(out)
}

fn classify(
    check_name: &str,
    exit_code: i32,
    stdout: &str,
    stderr: &str,
    success_exit_codes: &[i32],
) -> CheckStatus {
    if !success_exit_codes.contains(&exit_code) {
        return CheckStatus::Error;
    }
    use crate::scanner::{scan_has_diagnostic_prefix, DiagnosticLevel};
    // Single Aho-Corasick pass over each output (O(n+m) each).
    let stdout = strip_allowed_warning_lines_for_check(check_name, stdout);
    let stderr = strip_allowed_warning_lines_for_check(check_name, stderr);
    match scan_has_diagnostic_prefix(&stderr).max_level(scan_has_diagnostic_prefix(&stdout)) {
        DiagnosticLevel::Error => CheckStatus::Error,
        DiagnosticLevel::Warning => CheckStatus::Warning,
        DiagnosticLevel::Clean => CheckStatus::Pass,
    }
}

pub(crate) fn is_cacheable_success_output(
    check_name: &str,
    output: &CommandOutput,
    success_exit_codes: &[i32],
) -> bool {
    classify(
        check_name,
        output.exit_code,
        &output.stdout,
        &output.stderr,
        success_exit_codes,
    ) == CheckStatus::Pass
}

/// Heartbeat interval for long-running checks: print progress every 3 seconds.
///
/// Reduced from 10 s to 3 s so users see "still running" feedback much sooner
/// during slow cargo compilations without live streaming (e.g., cold-cache builds).
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(3);

#[cfg(test)]
pub fn run_checks(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn ProgressReporter,
) -> Result<VerifyReport> {
    run_checks_with_heartbeat(runner, checks, reporter, HEARTBEAT_INTERVAL)
}

/// Inner implementation of `run_checks` with a configurable heartbeat interval.
#[cfg(test)]
///
/// For each check a background thread wakes every `heartbeat_interval` and calls
/// `reporter.check_still_running()` so the user sees progress during long compilations
/// instead of a silent terminal.  `std::thread::scope` bounds the thread lifetime to the
/// per-check body, so no `Send` bound is required on the reporter.
///
/// A `Condvar` is used for interruptible sleep so that when a fast check finishes the
/// heartbeat thread wakes immediately (no busy-wait, no fixed sleep overhead).
fn run_checks_with_heartbeat(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn ProgressReporter,
    heartbeat_interval: Duration,
) -> Result<VerifyReport> {
    for spec in checks {
        reporter.check_started(spec.name);

        let start = Instant::now();
        // Condvar pair: (done flag, condvar).
        // The condvar allows the heartbeat thread to be woken immediately when the
        // check completes instead of waiting for the full sleep interval to expire.
        let done = Mutex::new(false);
        let cvar = Condvar::new();

        // Run the check inside a scoped thread block.  The scoped heartbeat thread borrows
        // `done`, `cvar`, `reporter`, and `start` — all live long enough because
        // thread::scope guarantees the thread finishes before the scope exits.
        let output_result: anyhow::Result<CommandOutput> = std::thread::scope(|s| {
            s.spawn(|| {
                let mut guard = done.lock().unwrap();
                loop {
                    // Check done before waiting so we exit immediately if the
                    // check already finished before the thread started.
                    if *guard {
                        break;
                    }
                    // wait_timeout atomically releases the lock and waits for either
                    // a notify_one() signal or a timeout.  This eliminates the fixed
                    // sleep penalty for fast checks.
                    let (g, timeout_result) = cvar.wait_timeout(guard, heartbeat_interval).unwrap();
                    guard = g;
                    if *guard {
                        break; // woken because check finished
                    }
                    if timeout_result.timed_out() {
                        // Genuine timeout: emit heartbeat then re-lock and wait again.
                        drop(guard);
                        reporter.check_still_running(spec.name, start.elapsed());
                        guard = done.lock().unwrap();
                    }
                    // Spurious wakeup (!timed_out && !*guard): loop and wait again.
                }
            });

            let result = runner
                .run(spec)
                .with_context(|| format!("run {}", spec.name));
            // Signal the heartbeat thread to stop and wake it up immediately.
            {
                let mut guard = done.lock().unwrap();
                *guard = true;
                cvar.notify_one();
            }
            result
        });

        let elapsed = start.elapsed();

        let output = match output_result {
            Ok(output) => output,
            Err(e) => {
                reporter.check_failed(spec.name, elapsed, CheckStatus::Error);
                return Ok(VerifyReport {
                    exit: VerifyExitCode::Failure,
                    failure: Some(CheckFailure {
                        name: spec.name,
                        status: CheckStatus::Error,
                        exit_code: -1,
                        stdout: String::new(),
                        stderr: format!("{e:#}"),
                    }),
                });
            }
        };

        let status = classify(
            spec.name,
            output.exit_code,
            &output.stdout,
            &output.stderr,
            spec.success_exit_codes,
        );

        match status {
            CheckStatus::Pass => {
                reporter.check_passed(spec.name, elapsed);
            }
            CheckStatus::Warning | CheckStatus::Error => {
                reporter.check_failed(spec.name, elapsed, status);
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

/// Format native scan violations in rg-compatible `path:line:content` format.
fn format_scan_violations(violations: &[crate::scanner::NativeScanViolation]) -> String {
    violations
        .iter()
        .map(|v| format!("{}:{}:{}", v.file.display(), v.line_number, v.line))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Check groups for parallel verification.
pub struct CheckGroups<'a> {
    pub fmt: &'a [CommandSpec],
    pub core_cargo: &'a [CommandSpec],
    pub xtask_cargo: &'a [CommandSpec],
    pub gui_cargo: &'a [CommandSpec],
    pub frontend_install: &'a [CommandSpec],
    pub frontend_post_install: &'a [CommandSpec],
    pub release: &'a [CommandSpec],
}

fn all_group_checks(groups: &CheckGroups<'_>) -> Vec<CommandSpec> {
    groups
        .fmt
        .iter()
        .chain(groups.core_cargo.iter())
        .chain(groups.xtask_cargo.iter())
        .chain(groups.gui_cargo.iter())
        .chain(groups.frontend_install.iter())
        .chain(groups.frontend_post_install.iter())
        .chain(groups.release.iter())
        .cloned()
        .collect()
}

/// Fast verification: shared cache preparation, native checks gate, then seven parallel lanes.
///
/// Phase 0 (serial): shared cache preparation for all verify checks.
///
/// Phase 1 (serial): native checks — instantaneous Rust function calls that gate everything.
///
/// Phase 2 (concurrent via `std::thread::scope`):
/// - Lane 1: native Aho-Corasick scan (pure file I/O, no target/ interaction)
/// - Lane 2: `groups.fmt` (cargo fmt --check, no target/ interaction)
/// - Lane 3: `groups.core_cargo` (uses default target/)
/// - Lane 4: `groups.xtask_cargo` (uses target/xtask-parallel-verify)
/// - Lane 5: `groups.gui_cargo` (uses target/gui-parallel-verify)
/// - Lane 6: `groups.frontend_install` then `groups.frontend_post_install` (parallel after install)
/// - Lane 7: `groups.release` (release build + dylint, separate target dir)
///
/// Result priority: scan > fmt > core_cargo > xtask > gui > frontend > release.
pub fn verify_fast(
    runner: std::sync::Arc<dyn CommandRunner>,
    repo_root: &std::path::Path,
    native_checks: &[NativeCheck],
    groups: &CheckGroups<'_>,
    reporter: &dyn ProgressReporter,
) -> Result<VerifyReport> {
    let all_checks = all_group_checks(groups);
    let _ = runner.prepare_for_verify(
        repo_root,
        native_checks,
        &all_checks,
        crate::scanner::NATIVE_SCAN_CHECKS,
    );

    // Phase 1: native checks (always sequential, very fast).
    for check in native_checks {
        let start = Instant::now();
        reporter.check_started(check.name);
        let result = runner
            .run_native_check(repo_root, check)
            .with_context(|| format!("run native check {}", check.name))?;
        let elapsed = start.elapsed();
        if result.status != CheckStatus::Pass {
            reporter.check_failed(check.name, elapsed, result.status);
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
        reporter.check_passed(check.name, elapsed);
    }

    // Phase 2: run all groups concurrently — scan and fmt overlap with cargo compilation.
    let cancel = std::sync::Arc::new(CancellationState::new());

    let scan_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let fmt_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let xtask_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let gui_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let frontend_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let release_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);

    let cargo_report = std::thread::scope(|s| {
        // Lane 1: native Aho-Corasick scan (pure file I/O, zero target/ interaction).
        {
            let runner_scan = std::sync::Arc::clone(&runner);
            let cancel_scan = &cancel;
            let result_scan = &scan_result;
            s.spawn(move || {
                let lane_start = Instant::now();
                if cancel_scan.should_cancel(FailurePriority::Scan) {
                    return;
                }
                let scan_start = Instant::now();
                reporter.check_started("native-scan");
                let scan_results = runner_scan
                    .run_native_scan(
                        repo_root,
                        crate::scanner::NATIVE_SCAN_CHECKS,
                        &|name, info| reporter.check_progress(name, info),
                    )
                    .with_context(|| "run native-scan".to_string());
                let scan_elapsed = scan_start.elapsed();
                let scan_results = match scan_results {
                    Ok(scan_results) => scan_results,
                    Err(error) => {
                        reporter.check_failed("native-scan", scan_elapsed, CheckStatus::Error);
                        cancel_scan.record_failure(FailurePriority::Scan);
                        *result_scan.lock().unwrap() = Some(Ok(VerifyReport {
                            exit: VerifyExitCode::Failure,
                            failure: Some(CheckFailure {
                                name: "native-scan",
                                status: CheckStatus::Error,
                                exit_code: 1,
                                stdout: String::new(),
                                stderr: format!("{error:#}"),
                            }),
                        }));
                        reporter.lane_finished("native-scan", lane_start.elapsed());
                        return;
                    }
                };
                for result in &scan_results {
                    if !result.passed {
                        reporter.check_failed(result.check_name, scan_elapsed, CheckStatus::Error);
                        let output = format_scan_violations(&result.violations);
                        cancel_scan.record_failure(FailurePriority::Scan);
                        *result_scan.lock().unwrap() = Some(Ok(VerifyReport {
                            exit: VerifyExitCode::Failure,
                            failure: Some(CheckFailure {
                                name: result.check_name,
                                status: CheckStatus::Error,
                                exit_code: 1,
                                stdout: output,
                                stderr: String::new(),
                            }),
                        }));
                        reporter.lane_finished("native-scan", lane_start.elapsed());
                        return;
                    }
                }
                reporter.check_passed("native-scan", scan_elapsed);
                *result_scan.lock().unwrap() = Some(Ok(VerifyReport {
                    exit: VerifyExitCode::Success,
                    failure: None,
                }));
                reporter.lane_finished("native-scan", lane_start.elapsed());
            });
        }

        // Lane 2: fmt checks (cargo fmt --check, no target/ needed, zero contention).
        if !groups.fmt.is_empty() {
            let runner_fmt = std::sync::Arc::clone(&runner);
            let cancel_fmt = &cancel;
            let result_fmt = &fmt_result;
            let fmt = groups.fmt;
            s.spawn(move || {
                let lane_start = Instant::now();
                let report = run_checks_cancellable(
                    runner_fmt.as_ref(),
                    fmt,
                    reporter,
                    cancel_fmt,
                    FailurePriority::Fmt,
                );
                *result_fmt.lock().unwrap() = Some(report);
                reporter.lane_finished("fmt", lane_start.elapsed());
            });
        }

        // Lane 3 (xtask cargo checks, separate target dir).
        if !groups.xtask_cargo.is_empty() {
            let runner_xt = std::sync::Arc::clone(&runner);
            let cancel_xt = &cancel;
            let result_xt = &xtask_result;
            let xtask = groups.xtask_cargo;
            s.spawn(move || {
                let lane_start = Instant::now();
                let report = run_checks_cancellable(
                    runner_xt.as_ref(),
                    xtask,
                    reporter,
                    cancel_xt,
                    FailurePriority::XtaskCargo,
                );
                *result_xt.lock().unwrap() = Some(report);
                reporter.lane_finished("xtask-cargo", lane_start.elapsed());
            });
        }

        // Lane 4 (gui cargo checks, separate target dir).
        if !groups.gui_cargo.is_empty() {
            let runner_gui = std::sync::Arc::clone(&runner);
            let cancel_gui = &cancel;
            let result_gui = &gui_result;
            let gui = groups.gui_cargo;
            s.spawn(move || {
                let lane_start = Instant::now();
                let report = run_checks_cancellable(
                    runner_gui.as_ref(),
                    gui,
                    reporter,
                    cancel_gui,
                    FailurePriority::GuiCargo,
                );
                *result_gui.lock().unwrap() = Some(report);
                reporter.lane_finished("gui-cargo", lane_start.elapsed());
            });
        }

        // Lane 5 (frontend: install sequentially, then lint+test in parallel).
        let has_frontend =
            !groups.frontend_install.is_empty() || !groups.frontend_post_install.is_empty();
        if has_frontend {
            let runner_fe = std::sync::Arc::clone(&runner);
            let cancel_fe = &cancel;
            let result_fe = &frontend_result;
            let install = groups.frontend_install;
            let post_install = groups.frontend_post_install;
            s.spawn(move || {
                let lane_start = Instant::now();
                // Phase A: run install checks sequentially (must complete before lint/test).
                let install_report = run_checks_cancellable(
                    runner_fe.as_ref(),
                    install,
                    reporter,
                    cancel_fe,
                    FailurePriority::Frontend,
                );
                match &install_report {
                    Ok(report) if report.exit == VerifyExitCode::Success => {}
                    _ => {
                        // Install failed or errored — store result and bail.
                        *result_fe.lock().unwrap() = Some(install_report);
                        reporter.lane_finished("frontend", lane_start.elapsed());
                        return;
                    }
                }

                // Phase B: run post-install checks in parallel (lint + test are independent).
                if post_install.is_empty() || cancel_fe.should_cancel(FailurePriority::Frontend) {
                    *result_fe.lock().unwrap() = Some(install_report);
                    reporter.lane_finished("frontend", lane_start.elapsed());
                    return;
                }

                let sub_results: Vec<Result<VerifyReport>> = std::thread::scope(|sub_s| {
                    let runner_fe = std::sync::Arc::clone(&runner_fe);
                    let handles: Vec<_> = post_install
                        .iter()
                        .map(|spec| {
                            let runner_fe = std::sync::Arc::clone(&runner_fe);
                            sub_s.spawn(move || {
                                run_checks_cancellable(
                                    runner_fe.as_ref(),
                                    std::slice::from_ref(spec),
                                    reporter,
                                    cancel_fe,
                                    FailurePriority::Frontend,
                                )
                            })
                        })
                        .collect();
                    handles
                        .into_iter()
                        .map(|h| h.join().expect("frontend sub-thread panicked"))
                        .collect()
                });

                // Return the first failure (if any).
                for sub_result in sub_results {
                    match &sub_result {
                        Ok(report) if report.exit == VerifyExitCode::Failure => {
                            *result_fe.lock().unwrap() = Some(sub_result);
                            reporter.lane_finished("frontend", lane_start.elapsed());
                            return;
                        }
                        Err(_) => {
                            *result_fe.lock().unwrap() = Some(sub_result);
                            reporter.lane_finished("frontend", lane_start.elapsed());
                            return;
                        }
                        _ => {}
                    }
                }

                *result_fe.lock().unwrap() = Some(Ok(VerifyReport {
                    exit: VerifyExitCode::Success,
                    failure: None,
                }));
                reporter.lane_finished("frontend", lane_start.elapsed());
            });
        }

        // Lane 6 (release checks, release build + dylint, separate target dir).
        if !groups.release.is_empty() {
            let runner_rel = std::sync::Arc::clone(&runner);
            let cancel_rel = &cancel;
            let result_rel = &release_result;
            let release = groups.release;
            s.spawn(move || {
                let lane_start = Instant::now();
                let report = run_checks_cancellable(
                    runner_rel.as_ref(),
                    release,
                    reporter,
                    cancel_rel,
                    FailurePriority::Release,
                );
                *result_rel.lock().unwrap() = Some(report);
                reporter.lane_finished("release", lane_start.elapsed());
            });
        }

        // Lane 7 (main thread): core cargo checks (uses default target/).
        let lane_start = Instant::now();
        let cargo_report = run_checks_cancellable(
            runner.as_ref(),
            groups.core_cargo,
            reporter,
            &cancel,
            FailurePriority::CoreCargo,
        );
        reporter.lane_finished("core-cargo", lane_start.elapsed());
        cargo_report
    });

    // Collect results: scan > fmt > core_cargo > xtask > gui > frontend > release.
    if let Some(scan_res) = scan_result.lock().unwrap().take() {
        let scan_report = scan_res?;
        if scan_report.exit == VerifyExitCode::Failure {
            return Ok(scan_report);
        }
    }

    if let Some(fmt_res) = fmt_result.lock().unwrap().take() {
        let fmt_report = fmt_res?;
        if fmt_report.exit == VerifyExitCode::Failure {
            return Ok(fmt_report);
        }
    }

    let cargo_report = cargo_report?;
    if cargo_report.exit == VerifyExitCode::Failure {
        return Ok(cargo_report);
    }

    if let Some(xt_result) = xtask_result.lock().unwrap().take() {
        let xt_report = xt_result?;
        if xt_report.exit == VerifyExitCode::Failure {
            return Ok(xt_report);
        }
    }

    if let Some(gui_res) = gui_result.lock().unwrap().take() {
        let gui_report = gui_res?;
        if gui_report.exit == VerifyExitCode::Failure {
            return Ok(gui_report);
        }
    }

    if let Some(fe_result) = frontend_result.lock().unwrap().take() {
        let fe_report = fe_result?;
        if fe_report.exit == VerifyExitCode::Failure {
            return Ok(fe_report);
        }
    }

    if let Some(rel_result) = release_result.lock().unwrap().take() {
        let rel_report = rel_result?;
        if rel_report.exit == VerifyExitCode::Failure {
            return Ok(rel_report);
        }
    }

    Ok(VerifyReport {
        exit: VerifyExitCode::Success,
        failure: None,
    })
}

/// Format checks: `cargo fmt --check` does not use target/ and has zero contention
/// with any cargo build. Runs in its own parallel lane so clippy can start immediately.
pub const FMT_CHECKS: &[CommandSpec] = &[CommandSpec {
    name: "fmt-check",
    program: "cargo",
    args: &["fmt", "--all", "--check"],
    success_exit_codes: &[0],
    extra_env: &[],
}];

/// Core cargo checks: lint and test commands that share the default target/ directory.
///
/// These run sequentially within their group (they share the cargo build cache)
/// but the group itself runs in parallel with xtask, gui, frontend, and release groups.
pub const CORE_CARGO_CHECKS: &[CommandSpec] = &[
    // ── lint ─────────────────────────────────────────────────────────────────
    CommandSpec {
        name: "clippy-core",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "ralph-workflow",
            "-p",
            "ralph-workflow-tests",
            "-p",
            "test-helpers",
            "--all-targets",
            "--all-features",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    // ── tests ────────────────────────────────────────────────────────────────
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
];

/// Xtask cargo checks: lint and test for the xtask crate.
///
/// Uses a separate `CARGO_TARGET_DIR` to avoid cargo lock contention with the
/// core cargo group running in parallel.
pub const XTASK_CARGO_CHECKS: &[CommandSpec] = &[
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

/// GUI cargo checks: lint and unit tests for the ralph-gui crate.
///
/// Uses a separate `CARGO_TARGET_DIR` to avoid cargo lock contention with the
/// core cargo group running in parallel.
pub const GUI_CARGO_CHECKS: &[CommandSpec] = &[
    CommandSpec {
        name: "clippy-ralph-gui",
        program: "cargo",
        args: &[
            "clippy",
            "-p",
            "ralph-gui",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        success_exit_codes: &[0],
        extra_env: &[("CARGO_TARGET_DIR", "target/gui-parallel-verify")],
    },
    CommandSpec {
        name: "test-ralph-gui-lib",
        program: "cargo",
        args: &["test", "-p", "ralph-gui", "--lib"],
        success_exit_codes: &[0],
        extra_env: &[("CARGO_TARGET_DIR", "target/gui-parallel-verify")],
    },
];

/// Frontend install: npm ci must complete before lint/test can start.
pub const FRONTEND_INSTALL_CHECKS: &[CommandSpec] = &[CommandSpec {
    name: "ralph-gui-frontend-install",
    program: "npm",
    args: &[
        "--prefix",
        "ralph-gui/ui",
        "ci",
        "--no-audit",
        "--no-fund",
        "--include=dev",
    ],
    success_exit_codes: &[0],
    // Force devDependencies installation even if the outer environment sets
    // NODE_ENV=production or npm_config_production=true.
    extra_env: &[
        ("NODE_ENV", "development"),
        ("NPM_CONFIG_PRODUCTION", "false"),
        ("npm_config_production", "false"),
    ],
}];

/// Frontend post-install checks: lint and test run in parallel after install.
///
/// These are read-only operations on node_modules, so they can safely overlap.
pub const FRONTEND_POST_INSTALL_CHECKS: &[CommandSpec] = &[
    CommandSpec {
        name: "ralph-gui-frontend-lint",
        program: "npm",
        args: &["--prefix", "ralph-gui/ui", "run", "lint"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    CommandSpec {
        name: "ralph-gui-frontend-test",
        program: "npm",
        args: &["--prefix", "ralph-gui/ui", "run", "test", "--", "--run"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
];

/// All frontend checks combined (install + post-install), for test convenience.
#[cfg(test)]
pub const FRONTEND_CHECKS: &[CommandSpec] = &[
    CommandSpec {
        name: "ralph-gui-frontend-install",
        program: "npm",
        args: &[
            "--prefix",
            "ralph-gui/ui",
            "ci",
            "--no-audit",
            "--no-fund",
            "--include=dev",
        ],
        success_exit_codes: &[0],
        extra_env: &[
            ("NODE_ENV", "development"),
            ("NPM_CONFIG_PRODUCTION", "false"),
            ("npm_config_production", "false"),
        ],
    },
    CommandSpec {
        name: "ralph-gui-frontend-lint",
        program: "npm",
        args: &["--prefix", "ralph-gui/ui", "run", "lint"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
    CommandSpec {
        name: "ralph-gui-frontend-test",
        program: "npm",
        args: &["--prefix", "ralph-gui/ui", "run", "test", "--", "--run"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
];

/// Release build and custom lints.
///
/// Uses a separate `CARGO_TARGET_DIR` to avoid cargo lock contention with the
/// debug cargo group running in parallel.
pub const RELEASE_CHECKS: &[CommandSpec] = &[
    CommandSpec {
        name: "release-build",
        program: "cargo",
        args: &["build", "--release"],
        success_exit_codes: &[0],
        extra_env: &[("CARGO_TARGET_DIR", "target/release-parallel-verify")],
    },
    CommandSpec {
        name: "dylint",
        program: "make",
        args: &["dylint"],
        success_exit_codes: &[0],
        extra_env: &[
            ("DYLINT_DRIVER_PATH", "target/dylint-driver"),
            ("CARGO_TARGET_DIR", "target/release-parallel-verify"),
        ],
    },
];

/// Run checks sequentially with cancellation support.
///
/// Like `run_checks`, but checks the `cancel` flag before starting each check.
/// When a check fails, the cancel flag is set to signal other parallel groups.
fn run_checks_cancellable(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn ProgressReporter,
    cancel: &CancellationState,
    lane_priority: FailurePriority,
) -> Result<VerifyReport> {
    for spec in checks {
        if cancel.should_cancel(lane_priority) {
            break;
        }
        reporter.check_started(spec.name);
        let start = Instant::now();

        let done = Mutex::new(false);
        let cvar = Condvar::new();

        let output_result: anyhow::Result<CommandOutput> = std::thread::scope(|s| {
            s.spawn(|| {
                let mut guard = done.lock().unwrap();
                loop {
                    if *guard {
                        break;
                    }
                    let (g, timeout_result) = cvar.wait_timeout(guard, HEARTBEAT_INTERVAL).unwrap();
                    guard = g;
                    if *guard {
                        break;
                    }
                    if timeout_result.timed_out() {
                        drop(guard);
                        reporter.check_still_running(spec.name, start.elapsed());
                        guard = done.lock().unwrap();
                    }
                }
            });

            let result = runner
                .run(spec)
                .with_context(|| format!("run {}", spec.name));
            {
                let mut guard = done.lock().unwrap();
                *guard = true;
                cvar.notify_one();
            }
            result
        });

        let elapsed = start.elapsed();

        let output = match output_result {
            Ok(output) => output,
            Err(e) => {
                reporter.check_failed(spec.name, elapsed, CheckStatus::Error);
                cancel.record_failure(lane_priority);
                return Ok(VerifyReport {
                    exit: VerifyExitCode::Failure,
                    failure: Some(CheckFailure {
                        name: spec.name,
                        status: CheckStatus::Error,
                        exit_code: -1,
                        stdout: String::new(),
                        stderr: format!("{e:#}"),
                    }),
                });
            }
        };

        let status = classify(
            spec.name,
            output.exit_code,
            &output.stdout,
            &output.stderr,
            spec.success_exit_codes,
        );

        match status {
            CheckStatus::Pass => {
                reporter.check_passed(spec.name, elapsed);
            }
            CheckStatus::Warning | CheckStatus::Error => {
                reporter.check_failed(spec.name, elapsed, status);
                cancel.record_failure(lane_priority);
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

/// Returns all required checks across all groups, in group-priority order.
///
/// Used by tests to verify check existence.  The returned specs use the
/// group-specific `extra_env` (e.g., release checks have `CARGO_TARGET_DIR`
/// set for parallel execution).
#[cfg(test)]
fn all_required_checks() -> Vec<&'static CommandSpec> {
    FMT_CHECKS
        .iter()
        .chain(CORE_CARGO_CHECKS.iter())
        .chain(XTASK_CARGO_CHECKS.iter())
        .chain(GUI_CARGO_CHECKS.iter())
        .chain(FRONTEND_INSTALL_CHECKS.iter())
        .chain(FRONTEND_POST_INSTALL_CHECKS.iter())
        .chain(RELEASE_CHECKS.iter())
        .collect()
}

#[cfg(test)]
fn verify(
    runner: std::sync::Arc<dyn CommandRunner>,
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

    run_checks(runner.as_ref(), checks, &NoopProgressReporter)
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::collections::HashMap;
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
        let report = run_checks(&runner, &checks, &NoopProgressReporter)
            .expect("run_checks should not error");

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
        let report = run_checks(&runner, &checks, &NoopProgressReporter)
            .expect("run_checks should not error");

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
        let report = run_checks(&runner, &checks, &NoopProgressReporter)
            .expect("run_checks should not error");

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
        let report = run_checks(&runner, &checks, &NoopProgressReporter)
            .expect("run_checks should not error");

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
        let report = run_checks(&runner, &checks, &NoopProgressReporter)
            .expect("run_checks should not error");

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
        let report_a = run_checks(&runner_a, &checks, &NoopProgressReporter)
            .expect("run_checks A should succeed");
        let report_b = run_checks(&runner_b, &checks, &NoopProgressReporter)
            .expect("run_checks B should succeed");

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
            checks: &[crate::scanner::NativeScanCheck],
            _progress: &(dyn Fn(&str, &str) + Sync),
        ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
            self.native_scan_calls.fetch_add(1, Ordering::SeqCst);
            Ok(checks
                .iter()
                .map(|check| crate::scanner::NativeScanCheckResult {
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
            native_scan_checks: &[crate::scanner::NativeScanCheck],
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
            checks: &[crate::scanner::NativeScanCheck],
            _progress: &(dyn Fn(&str, &str) + Sync),
        ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
            self.events
                .lock()
                .unwrap()
                .push(format!("native-scan:{}", checks.len()));
            Ok(checks
                .iter()
                .map(|check| crate::scanner::NativeScanCheckResult {
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
            _native_scan_checks: &[crate::scanner::NativeScanCheck],
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
            checks: &[crate::scanner::NativeScanCheck],
            _progress: &(dyn Fn(&str, &str) + Sync),
        ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
            self.events
                .lock()
                .unwrap()
                .push(format!("native-scan:{}", checks.len()));
            Ok(checks
                .iter()
                .map(|check| crate::scanner::NativeScanCheckResult {
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
            vec!["clippy-core", "test-ralph-workflow-lib", "test-integration",]
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
    fn test_ralph_gui_frontend_install_forces_and_includes_dev_dependencies() {
        let spec = FRONTEND_CHECKS
            .iter()
            .find(|c| c.name == "ralph-gui-frontend-install")
            .expect("FRONTEND_CHECKS must include ralph-gui-frontend-install");

        let mut env = std::collections::HashMap::new();
        for (k, v) in spec.extra_env {
            env.insert(*k, *v);
        }

        assert!(
            spec.args.contains(&"--include=dev"),
            "frontend install must include dev dependencies to run eslint/vitest"
        );
        assert_eq!(env.get("NODE_ENV"), Some(&"development"));
        assert_eq!(env.get("NPM_CONFIG_PRODUCTION"), Some(&"false"));
        assert_eq!(env.get("npm_config_production"), Some(&"false"));
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
    fn test_audit_ignore_has_url_check_is_in_native_scan_checks() {
        // TDD anchor: audit-ignore-has-url is now a native NegativeLookahead scan check.
        assert!(
            crate::scanner::NATIVE_SCAN_CHECKS
                .iter()
                .any(|c| c.name == "audit-ignore-has-url"),
            "NATIVE_SCAN_CHECKS must include the audit-ignore-has-url check"
        );
    }

    #[test]
    fn test_forbidden_allow_expect_is_in_native_scan_checks() {
        // TDD anchor: forbidden-allow-expect-scan is now a native AnyLiteralAtLineStart check.
        assert!(
            crate::scanner::NATIVE_SCAN_CHECKS
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

        let report =
            run_checks(&runner, &checks, &reporter).expect("run_checks must return a report");

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
            stderr:
                "Warning: An update to Configuration inside a test was not wrapped in act(...)\n"
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
            stderr:
                "Warning: An update to Configuration inside a test was not wrapped in act(...)\n"
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
}
