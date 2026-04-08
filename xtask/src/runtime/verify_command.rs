#[cfg(test)]
use std::sync::Arc;
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result};

use super::policy::{
    strip_allowed_generated_harness_large_stack_frames, strip_allowed_warning_lines_for_check,
};
#[cfg(test)]
use super::progress::NoopProgressReporter;
use super::types::{
    CancellationState, CheckFailure, CheckStatus, FailurePriority, NativeCheck, NativeCheckResult,
    VerifyExitCode, VerifyReport,
};

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
        _native_scan_checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<()> {
        Ok(())
    }

    fn run_native_scan(
        &self,
        repo_root: &std::path::Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        Ok(crate::io::scanner::run_native_scan_checks_reporting(
            repo_root, checks, progress,
        ))
    }
}

pub(crate) fn classify(
    check_name: &str,
    exit_code: i32,
    stdout: &str,
    stderr: &str,
    success_exit_codes: &[i32],
) -> CheckStatus {
    let (stdout, stderr, allowed_nonzero_exit) = prepare_check_outputs(check_name, stdout, stderr);

    if !success_exit_codes.contains(&exit_code) && !allowed_nonzero_exit {
        return CheckStatus::Error;
    }

    determine_check_status(&stderr, &stdout)
}

fn prepare_check_outputs(check_name: &str, stdout: &str, stderr: &str) -> (String, String, bool) {
    let stdout = strip_allowed_warning_lines_for_check(check_name, stdout).into_owned();
    let (stderr, allowed_nonzero_exit) =
        strip_allowed_generated_harness_large_stack_frames(check_name, stderr);
    let stderr = strip_allowed_warning_lines_for_check(check_name, &stderr).into_owned();
    (stdout, stderr, allowed_nonzero_exit)
}

fn determine_check_status(stderr: &str, stdout: &str) -> CheckStatus {
    use crate::io::scanner::{scan_has_diagnostic_prefix, DiagnosticLevel};

    match scan_has_diagnostic_prefix(stderr).max_level(scan_has_diagnostic_prefix(stdout)) {
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

pub(crate) const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(3);

#[cfg(test)]
pub fn run_checks(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn super::progress::ProgressReporter,
) -> Result<VerifyReport> {
    run_checks_with_heartbeat(runner, checks, reporter, HEARTBEAT_INTERVAL)
}

#[cfg(test)]
pub(crate) fn verify(
    runner: Arc<dyn CommandRunner>,
    repo_root: &std::path::Path,
    native_checks: &[NativeCheck],
    checks: &[CommandSpec],
) -> Result<VerifyReport> {
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
pub(crate) fn run_checks_with_heartbeat(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn super::progress::ProgressReporter,
    heartbeat_interval: Duration,
) -> Result<VerifyReport> {
    for spec in checks {
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
                    let (g, timeout_result) = cvar.wait_timeout(guard, heartbeat_interval).unwrap();
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

pub(crate) fn run_checks_cancellable(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn super::progress::ProgressReporter,
    cancel: &CancellationState,
    lane_priority: FailurePriority,
) -> Result<VerifyReport> {
    match process_checks(runner, checks, reporter, cancel, lane_priority)? {
        Some(report) => Ok(report),
        None => Ok(VerifyReport {
            exit: VerifyExitCode::Success,
            failure: None,
        }),
    }
}

fn process_checks(
    runner: &(dyn CommandRunner + Sync),
    checks: &[CommandSpec],
    reporter: &dyn super::progress::ProgressReporter,
    cancel: &CancellationState,
    lane_priority: FailurePriority,
) -> Result<Option<VerifyReport>> {
    for spec in checks {
        if cancel.should_cancel(lane_priority) {
            break;
        }
        if let Some(report) = run_single_check(runner, reporter, cancel, spec, lane_priority)? {
            return Ok(Some(report));
        }
    }
    Ok(None)
}

fn run_single_check(
    runner: &(dyn CommandRunner + Sync),
    reporter: &dyn super::progress::ProgressReporter,
    cancel: &CancellationState,
    spec: &CommandSpec,
    lane_priority: FailurePriority,
) -> Result<Option<VerifyReport>> {
    reporter.check_started(spec.name);
    let start = Instant::now();

    let output = match capture_command_output(runner, reporter, cancel, spec, lane_priority, start)
    {
        Ok(output) => output,
        Err(report) => return Ok(Some(report)),
    };

    run_check_status(
        reporter,
        cancel,
        spec,
        lane_priority,
        &output,
        start.elapsed(),
    )
}

fn run_command_with_heartbeat(
    runner: &(dyn CommandRunner + Sync),
    reporter: &dyn super::progress::ProgressReporter,
    spec: &CommandSpec,
    start: Instant,
) -> anyhow::Result<CommandOutput> {
    let done = Mutex::new(false);
    let cvar = Condvar::new();

    std::thread::scope(|s| {
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
    })
}

fn capture_command_output(
    runner: &(dyn CommandRunner + Sync),
    reporter: &dyn super::progress::ProgressReporter,
    cancel: &CancellationState,
    spec: &CommandSpec,
    lane_priority: FailurePriority,
    start: Instant,
) -> Result<CommandOutput, VerifyReport> {
    let result = run_command_with_heartbeat(runner, reporter, spec, start);
    let elapsed = start.elapsed();

    match result {
        Ok(output) => Ok(output),
        Err(e) => {
            reporter.check_failed(spec.name, elapsed, CheckStatus::Error);
            cancel.record_failure(lane_priority);
            Err(VerifyReport {
                exit: VerifyExitCode::Failure,
                failure: Some(CheckFailure {
                    name: spec.name,
                    status: CheckStatus::Error,
                    exit_code: -1,
                    stdout: String::new(),
                    stderr: format!("{e:#}"),
                }),
            })
        }
    }
}

fn run_check_status(
    reporter: &dyn super::progress::ProgressReporter,
    cancel: &CancellationState,
    spec: &CommandSpec,
    lane_priority: FailurePriority,
    output: &CommandOutput,
    elapsed: std::time::Duration,
) -> Result<Option<VerifyReport>> {
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
            Ok(None)
        }
        CheckStatus::Warning | CheckStatus::Error => {
            reporter.check_failed(spec.name, elapsed, status);
            cancel.record_failure(lane_priority);
            Ok(Some(VerifyReport {
                exit: VerifyExitCode::Failure,
                failure: Some(CheckFailure {
                    name: spec.name,
                    status,
                    exit_code: output.exit_code,
                    stdout: output.stdout.clone(),
                    stderr: output.stderr.clone(),
                }),
            }))
        }
    }
}
