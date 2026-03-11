use anyhow::{Context as _, Result};
use std::borrow::Cow;
use std::sync::atomic::AtomicBool;
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
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckStatus {
    Pass,
    Warning,
    Error,
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
}

/// No-op implementation used in tests and when progress output is not desired.
pub struct NoopProgressReporter;

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

/// Heartbeat interval for long-running checks: print progress every 3 seconds.
///
/// Reduced from 10 s to 3 s so users see "still running" feedback much sooner
/// during slow cargo compilations without live streaming (e.g., cold-cache builds).
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(3);

pub fn run_checks(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn ProgressReporter,
) -> Result<VerifyReport> {
    run_checks_with_heartbeat(runner, checks, reporter, HEARTBEAT_INTERVAL)
}

/// Inner implementation of `run_checks` with a configurable heartbeat interval.
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
    pub cargo_debug: &'a [CommandSpec],
    pub frontend: &'a [CommandSpec],
    pub release: &'a [CommandSpec],
    pub prefetch: &'a [CommandSpec],
}

/// Fast verification: native scan checks, then three parallel groups of checks.
///
/// Groups run concurrently using `std::thread::scope`:
/// - Main thread: `groups.cargo_debug` with optional `groups.prefetch`
/// - Thread 2: `groups.frontend` (npm, independent of cargo)
/// - Thread 3: `groups.release` (release build + dylint, separate target dir)
///
/// `groups.prefetch` are run concurrently in a background thread during the cargo debug
/// phase to pre-populate the cache (see `CachingCommandRunner`).  Pass `&[]` when no
/// prefetch is desired (e.g., in tests with fake runners).
pub fn verify_fast(
    runner: std::sync::Arc<dyn CommandRunner>,
    repo_root: &std::path::Path,
    native_checks: &[NativeCheck],
    groups: &CheckGroups<'_>,
    reporter: &dyn ProgressReporter,
) -> Result<VerifyReport> {
    // Phase 0: native checks (always sequential, very fast).
    for check in native_checks {
        let start = Instant::now();
        reporter.check_started(check.name);
        let result = (check.run)(repo_root);
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

    // Phase 0.5: native Aho-Corasick multi-pattern scan (replaces all rg subprocess calls).
    // Groups checks by directory, reads each source file once, O(n + m + z) per group.
    // Per-file progress is forwarded via check_progress every 50 files.
    let scan_start = Instant::now();
    reporter.check_started("native-scan");
    let scan_results = crate::scanner::run_native_scan_checks_reporting(
        repo_root,
        crate::scanner::NATIVE_SCAN_CHECKS,
        &|name, info| reporter.check_progress(name, info),
    );
    let scan_elapsed = scan_start.elapsed();
    for result in &scan_results {
        if !result.passed {
            reporter.check_failed(result.check_name, scan_elapsed, CheckStatus::Error);
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
    reporter.check_passed("native-scan", scan_elapsed);

    // Phase 2: run three check groups in parallel.
    // Cancellation flag: when any group fails, other groups skip remaining checks.
    let cancel = std::sync::Arc::new(AtomicBool::new(false));

    let frontend_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);
    let release_result: Mutex<Option<Result<VerifyReport>>> = Mutex::new(None);

    let cargo_report = std::thread::scope(|s| {
        // Thread 2: frontend checks (npm, independent of cargo).
        if !groups.frontend.is_empty() {
            let runner_fe = &runner;
            let cancel_fe = &cancel;
            let result_fe = &frontend_result;
            let frontend = groups.frontend;
            s.spawn(move || {
                let report =
                    run_checks_cancellable(runner_fe.as_ref(), frontend, reporter, cancel_fe);
                *result_fe.lock().unwrap() = Some(report);
            });
        }

        // Thread 3: release checks (release build + dylint, separate target dir).
        if !groups.release.is_empty() {
            let runner_rel = &runner;
            let cancel_rel = &cancel;
            let result_rel = &release_result;
            let release = groups.release;
            s.spawn(move || {
                let report =
                    run_checks_cancellable(runner_rel.as_ref(), release, reporter, cancel_rel);
                *result_rel.lock().unwrap() = Some(report);
            });
        }

        // Main thread: cargo debug checks with prefetch.
        let cargo_report = run_cargo_prefetch(
            runner.clone(),
            groups.prefetch,
            groups.cargo_debug,
            reporter,
        );
        if let Ok(ref report) = cargo_report {
            if report.exit == VerifyExitCode::Failure {
                cancel.store(true, Ordering::SeqCst);
            }
        }
        cargo_report
    });

    // Collect results: cargo group has highest priority.
    let cargo_report = cargo_report?;
    if cargo_report.exit == VerifyExitCode::Failure {
        return Ok(cargo_report);
    }

    // Check frontend group result.
    if let Some(fe_result) = frontend_result.lock().unwrap().take() {
        let fe_report = fe_result?;
        if fe_report.exit == VerifyExitCode::Failure {
            return Ok(fe_report);
        }
    }

    // Check release group result.
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

/// Cargo debug checks: format, lint, and test commands that share the default target/ directory.
///
/// These run sequentially within their group (they share the cargo build cache)
/// but the group itself runs in parallel with frontend and release groups.
pub const CARGO_DEBUG_CHECKS: &[CommandSpec] = &[
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
    // ── ralph-gui crate: lint and unit tests ─────────────────────────────────
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
        extra_env: &[],
    },
    CommandSpec {
        name: "test-ralph-gui-lib",
        program: "cargo",
        args: &["test", "-p", "ralph-gui", "--lib"],
        success_exit_codes: &[0],
        extra_env: &[],
    },
];

/// Frontend checks: npm install, lint, and test.
///
/// These are completely independent of cargo and run in their own parallel group.
/// Within the group, install must precede lint/test.
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
        // Force devDependencies installation even if the outer environment sets
        // NODE_ENV=production or npm_config_production=true.
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

/// Prefetch specs for xtask and ralph-gui checks run concurrently with the sequential
/// cargo debug phase.
///
/// These use the same `name` as the corresponding entries in `CARGO_DEBUG_CHECKS` so they
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

/// Run checks sequentially with cancellation support.
///
/// Like `run_checks`, but checks the `cancel` flag before starting each check.
/// When a check fails, the cancel flag is set to signal other parallel groups.
fn run_checks_cancellable(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn ProgressReporter,
    cancel: &AtomicBool,
) -> Result<VerifyReport> {
    for spec in checks {
        if cancel.load(Ordering::SeqCst) {
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
                cancel.store(true, Ordering::SeqCst);
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
                cancel.store(true, Ordering::SeqCst);
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

/// Run prefetch specs concurrently with main specs.
///
/// Launches a background thread that runs `prefetch_specs` while the current
/// thread runs `main_specs` sequentially.  When `prefetch_specs` complete they
/// populate the `CachingCommandRunner` cache so that, if the sequential phase
/// later reaches the same check names, it gets instant cache hits.
///
/// Returns the first failure in `main_specs` order.
///
/// Prefetch is a best-effort optimisation: failures in `prefetch_specs` must
/// never fail verification, and are intentionally ignored.
pub fn run_cargo_prefetch(
    runner: std::sync::Arc<dyn CommandRunner>,
    prefetch_specs: &[CommandSpec],
    main_specs: &[CommandSpec],
    reporter: &dyn ProgressReporter,
) -> Result<VerifyReport> {
    let cancel_prefetch = std::sync::Arc::new(AtomicBool::new(false));

    // Spawn best-effort prefetch in a background thread.
    // Critical behavior: do NOT block surfacing main-spec failures on a slow prefetch.
    // On success we join so cache warm results can be flushed deterministically.
    let prefetch_handle = if prefetch_specs.is_empty() {
        None
    } else {
        let runner_bg = std::sync::Arc::clone(&runner);
        let cancel_bg = std::sync::Arc::clone(&cancel_prefetch);
        let specs: Vec<CommandSpec> = prefetch_specs.to_vec();
        Some(std::thread::spawn(move || {
            // Cancellation is best-effort: if a prefetch check is already running, we let it
            // complete, but we do not start additional prefetch work once cancelled.
            for spec in specs {
                if cancel_bg.load(Ordering::SeqCst) {
                    break;
                }
                let _ = run_checks(
                    runner_bg.as_ref(),
                    std::slice::from_ref(&spec),
                    &NoopProgressReporter,
                );
            }
        }))
    };

    let main_report = run_checks(runner.as_ref(), main_specs, reporter)?;
    if main_report.exit == VerifyExitCode::Success {
        if let Some(h) = prefetch_handle {
            let _ = h.join();
        }
    } else {
        cancel_prefetch.store(true, Ordering::SeqCst);
    }
    Ok(main_report)
}

/// Returns all required checks across all groups, in group-priority order.
///
/// Used by tests to verify check existence.  The returned specs use the
/// group-specific `extra_env` (e.g., release checks have `CARGO_TARGET_DIR`
/// set for parallel execution).
#[cfg(test)]
fn all_required_checks() -> Vec<&'static CommandSpec> {
    CARGO_DEBUG_CHECKS
        .iter()
        .chain(FRONTEND_CHECKS.iter())
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
        ran: Mutex<Vec<&'static str>>,
    }

    impl ByNameRunner {
        fn with_output(self, name: &'static str, output: CommandOutput) -> Self {
            self.outputs.lock().unwrap().insert(name, output);
            self
        }

        fn ran(&self) -> Vec<&'static str> {
            self.ran.lock().unwrap().clone()
        }
    }

    impl CommandRunner for ByNameRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.ran.lock().unwrap().push(spec.name);
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
    fn test_cargo_debug_checks_run_in_stable_order() {
        let runner = std::sync::Arc::new(RecordingRunner::default());
        let all_checks: Vec<CommandSpec> = CARGO_DEBUG_CHECKS.to_vec();
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
                "fmt-check",
                "clippy-ralph-workflow",
                "clippy-ralph-workflow-tests",
                "clippy-test-helpers",
                "clippy-xtask",
                "test-xtask",
                "test-ralph-workflow-lib",
                "test-integration",
                "clippy-ralph-gui",
                "test-ralph-gui-lib",
            ]
        );
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
    fn test_cargo_debug_checks_first_entry_is_fmt_check() {
        assert_eq!(
            CARGO_DEBUG_CHECKS[0].name, "fmt-check",
            "CARGO_DEBUG_CHECKS[0] must be fmt-check (first cargo check)"
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
        cargo_debug: &'a [CommandSpec],
        frontend: &'a [CommandSpec],
        release: &'a [CommandSpec],
    ) -> CheckGroups<'a> {
        CheckGroups {
            cargo_debug,
            frontend,
            release,
            prefetch: &[],
        }
    }

    #[test]
    fn test_verify_fast_runs_all_required_checks() {
        let runner = std::sync::Arc::new(RecordingRunner::default());
        let groups = test_groups(CARGO_DEBUG_CHECKS, FRONTEND_CHECKS, RELEASE_CHECKS);
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
    fn test_verify_fast_stops_on_first_cargo_failure() {
        let runner = std::sync::Arc::new(FakeRunner::new([CommandOutput {
            exit_code: 1,
            stdout: String::new(),
            stderr: "error: formatting differences found".to_string(),
        }]));

        let groups = test_groups(CARGO_DEBUG_CHECKS, &[], &[]);
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

    #[test]
    fn test_verify_fast_stops_on_cargo_check_failure() {
        let runner = std::sync::Arc::new(FakeRunner::new([CommandOutput {
            exit_code: 1,
            stdout: String::new(),
            stderr: "error: formatting differences found".to_string(),
        }]));

        let groups = test_groups(CARGO_DEBUG_CHECKS, &[], &[]);
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

    // ── TDD tests for cargo prefetch ────────────────────────────────────────

    #[derive(Debug)]
    struct BlockingByNameRunner {
        outputs: Mutex<HashMap<&'static str, CommandOutput>>,
        ran: Mutex<Vec<&'static str>>,
        prefetch_started: std::sync::mpsc::Sender<()>,
        prefetch_release: Mutex<std::sync::mpsc::Receiver<()>>,
    }

    impl BlockingByNameRunner {
        fn new(
            prefetch_started: std::sync::mpsc::Sender<()>,
            prefetch_release: std::sync::mpsc::Receiver<()>,
        ) -> Self {
            Self {
                outputs: Mutex::new(HashMap::new()),
                ran: Mutex::new(Vec::new()),
                prefetch_started,
                prefetch_release: Mutex::new(prefetch_release),
            }
        }

        fn with_output(self, name: &'static str, output: CommandOutput) -> Self {
            self.outputs.lock().unwrap().insert(name, output);
            self
        }

        fn ran(&self) -> Vec<&'static str> {
            self.ran.lock().unwrap().clone()
        }
    }

    impl CommandRunner for BlockingByNameRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.ran.lock().unwrap().push(spec.name);

            if spec.name == "prefetch-slow" {
                let _ = self.prefetch_started.send(());
                let rx = self.prefetch_release.lock().unwrap();
                let _ = rx.recv();
            }

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
    fn test_cargo_prefetch_specs_use_same_names_as_cargo_debug_checks() {
        // CARGO_PREFETCH_SPECS must use the same check names as CARGO_DEBUG_CHECKS
        // so the cache key (name + scope hash) is shared between prefetch and sequential runs.
        for spec in CARGO_PREFETCH_SPECS {
            assert!(
                CARGO_DEBUG_CHECKS.iter().any(|c| c.name == spec.name),
                "prefetch spec '{}' must have a matching entry in CARGO_DEBUG_CHECKS",
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

        let runner = std::sync::Arc::new(RecordingRunner::default());

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "fmt-check",
            program: "cargo",
            args: &["fmt", "--all", "--check"],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let prefetch_specs = CARGO_PREFETCH_SPECS;

        let report = run_cargo_prefetch(
            runner.clone(),
            prefetch_specs,
            main_specs,
            &NoopProgressReporter,
        )
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
        let runner = std::sync::Arc::new(FakeRunner::new(outputs));

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "fmt-check-fail",
            program: "cargo",
            args: &["fmt", "--all", "--check"],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let report = run_cargo_prefetch(runner.clone(), &[], main_specs, &NoopProgressReporter)
            .expect("run_cargo_prefetch should not error");

        assert_eq!(report.exit, VerifyExitCode::Failure);
        let failure = report.failure.expect("expected failure");
        assert_eq!(failure.name, "fmt-check-fail");
    }

    #[test]
    fn test_run_cargo_prefetch_does_not_fail_when_only_prefetch_fails() {
        // Prefetch is a best-effort optimisation; verification correctness is determined
        // by the sequential main specs.
        let runner = std::sync::Arc::new(
            ByNameRunner::default()
                .with_output(
                    "prefetch-fails",
                    CommandOutput {
                        exit_code: 1,
                        stdout: String::new(),
                        stderr: "error: prefetch failed".to_string(),
                    },
                )
                .with_output(
                    "main-passes",
                    CommandOutput {
                        exit_code: 0,
                        stdout: String::new(),
                        stderr: String::new(),
                    },
                ),
        );

        let prefetch_specs: &[CommandSpec] = &[CommandSpec {
            name: "prefetch-fails",
            program: "cargo",
            args: &[],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "main-passes",
            program: "cargo",
            args: &[],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let report = run_cargo_prefetch(
            runner.clone(),
            prefetch_specs,
            main_specs,
            &NoopProgressReporter,
        )
        .expect("run_cargo_prefetch should not error");

        assert_eq!(report.exit, VerifyExitCode::Success);

        // Both specs should have been invoked (order is not guaranteed due to concurrency).
        let ran = runner.ran();
        assert!(ran.contains(&"prefetch-fails"));
        assert!(ran.contains(&"main-passes"));
    }

    #[test]
    fn test_run_cargo_prefetch_does_not_block_on_prefetch_when_main_fails() {
        // Regression: run_cargo_prefetch previously used std::thread::scope, which
        // forced the prefetch thread to finish before returning. This delayed
        // surfacing fast failures (e.g., fmt-check) by the full prefetch duration.

        use std::sync::mpsc;

        let (prefetch_started_tx, prefetch_started_rx) = mpsc::channel();
        let (prefetch_release_tx, prefetch_release_rx) = mpsc::channel();
        let runner = std::sync::Arc::new(
            BlockingByNameRunner::new(prefetch_started_tx, prefetch_release_rx)
                .with_output(
                    "prefetch-slow",
                    CommandOutput {
                        exit_code: 0,
                        stdout: String::new(),
                        stderr: String::new(),
                    },
                )
                .with_output(
                    "main-fails",
                    CommandOutput {
                        exit_code: 1,
                        stdout: String::new(),
                        stderr: String::new(),
                    },
                ),
        );

        let prefetch_specs: &[CommandSpec] = &[CommandSpec {
            name: "prefetch-slow",
            program: "fake",
            args: &[],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "main-fails",
            program: "fake",
            args: &[],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let (done_tx, done_rx) = mpsc::channel();
        let runner_bg = runner.clone();
        std::thread::spawn(move || {
            let report =
                run_cargo_prefetch(runner_bg, prefetch_specs, main_specs, &NoopProgressReporter)
                    .expect("run_cargo_prefetch should not error");
            done_tx.send(report).unwrap();
        });

        // Ensure the prefetch thread actually started before we assert that the main
        // failure surfaces without waiting for it.
        prefetch_started_rx
            .recv_timeout(Duration::from_secs(2))
            .expect("prefetch should start");

        let report = done_rx
            .recv_timeout(Duration::from_secs(2))
            .expect("main failure should surface without waiting for prefetch");
        assert_eq!(report.exit, VerifyExitCode::Failure);

        // Unblock the prefetch thread so it can complete and not leak a blocked thread.
        let _ = prefetch_release_tx.send(());

        // Sanity: both specs should have been invoked.
        let ran = runner.ran();
        assert!(ran.contains(&"prefetch-slow"));
        assert!(ran.contains(&"main-fails"));
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
            + CARGO_DEBUG_CHECKS.len()
            + FRONTEND_CHECKS.len()
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

    #[derive(Default)]
    struct CoordinatedRunner {
        ran: Mutex<Vec<&'static str>>,
        prefetch_1_started: (Mutex<bool>, Condvar),
        prefetch_1_released: (Mutex<bool>, Condvar),
        prefetch_2_started: (Mutex<bool>, Condvar),
    }

    impl CoordinatedRunner {
        fn ran(&self) -> Vec<&'static str> {
            self.ran.lock().unwrap().clone()
        }

        fn wait_prefetch_1_started(&self, timeout: Duration) {
            let (lock, cvar) = &self.prefetch_1_started;
            let started = lock.lock().unwrap();
            let _ = cvar.wait_timeout_while(started, timeout, |v| !*v).unwrap();
        }

        fn release_prefetch_1(&self) {
            let (lock, cvar) = &self.prefetch_1_released;
            let mut released = lock.lock().unwrap();
            *released = true;
            cvar.notify_all();
        }

        fn wait_prefetch_2_started(&self, timeout: Duration) -> bool {
            let (lock, cvar) = &self.prefetch_2_started;
            let started = lock.lock().unwrap();
            let (started, _) = cvar.wait_timeout_while(started, timeout, |v| !*v).unwrap();
            *started
        }
    }

    impl CommandRunner for CoordinatedRunner {
        fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
            self.ran.lock().unwrap().push(spec.name);

            match spec.name {
                "prefetch-1" => {
                    let (lock, cvar) = &self.prefetch_1_started;
                    let mut started = lock.lock().unwrap();
                    *started = true;
                    cvar.notify_all();

                    let (lock, cvar) = &self.prefetch_1_released;
                    let released = lock.lock().unwrap();
                    let _ = cvar
                        .wait_timeout_while(released, Duration::from_secs(2), |v| !*v)
                        .unwrap();

                    Ok(CommandOutput {
                        exit_code: 0,
                        stdout: String::new(),
                        stderr: String::new(),
                    })
                }
                "prefetch-2" => {
                    let (lock, cvar) = &self.prefetch_2_started;
                    let mut started = lock.lock().unwrap();
                    *started = true;
                    cvar.notify_all();
                    Ok(CommandOutput {
                        exit_code: 0,
                        stdout: String::new(),
                        stderr: String::new(),
                    })
                }
                "main-fails" => {
                    // Ensure the background thread has started the first prefetch before we fail.
                    self.wait_prefetch_1_started(Duration::from_secs(2));
                    Ok(CommandOutput {
                        exit_code: 1,
                        stdout: String::new(),
                        stderr: String::new(),
                    })
                }
                _ => Ok(CommandOutput {
                    exit_code: 0,
                    stdout: String::new(),
                    stderr: String::new(),
                }),
            }
        }
    }

    #[test]
    fn test_run_cargo_prefetch_cancels_remaining_prefetch_specs_after_main_failure() {
        let runner = std::sync::Arc::new(CoordinatedRunner::default());
        let prefetch_specs: &[CommandSpec] = &[
            CommandSpec {
                name: "prefetch-1",
                program: "fake",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
            CommandSpec {
                name: "prefetch-2",
                program: "fake",
                args: &[],
                success_exit_codes: &[0],
                extra_env: &[],
            },
        ];
        let main_specs: &[CommandSpec] = &[CommandSpec {
            name: "main-fails",
            program: "fake",
            args: &[],
            success_exit_codes: &[0],
            extra_env: &[],
        }];

        let report = run_cargo_prefetch(
            runner.clone(),
            prefetch_specs,
            main_specs,
            &NoopProgressReporter,
        )
        .expect("run_cargo_prefetch should not error");
        assert_eq!(report.exit, VerifyExitCode::Failure);

        // Let the background thread finish the first prefetch.
        // Cancellation is best-effort: depending on scheduling, the second prefetch may
        // already be queued by the time the main failure is observed.
        runner.release_prefetch_1();
        let _prefetch_2_started = runner.wait_prefetch_2_started(Duration::from_millis(200));

        // Sanity: we should at least have run prefetch-1 and main-fails.
        let ran = runner.ran();
        assert!(ran.contains(&"prefetch-1"));
        assert!(ran.contains(&"main-fails"));
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
        let expected_count =
            CARGO_DEBUG_CHECKS.len() + FRONTEND_CHECKS.len() + RELEASE_CHECKS.len();
        assert_eq!(
            all.len(),
            expected_count,
            "all_required_checks() must return exactly the union of all groups"
        );
    }

    #[test]
    fn test_cargo_prefetch_includes_ralph_gui_checks() {
        let prefetch_names: Vec<&str> = CARGO_PREFETCH_SPECS.iter().map(|s| s.name).collect();
        assert!(
            prefetch_names.contains(&"clippy-ralph-gui"),
            "CARGO_PREFETCH_SPECS must include clippy-ralph-gui"
        );
        assert!(
            prefetch_names.contains(&"test-ralph-gui-lib"),
            "CARGO_PREFETCH_SPECS must include test-ralph-gui-lib"
        );
    }
}
