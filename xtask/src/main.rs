mod cache;
mod compliance;
mod scanner;
mod verify;

use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};
use std::sync::Arc;

use verify::{CommandOutput, CommandRunner, CommandSpec, ProgressReporter, VerifyExitCode};

fn drain_reader_lines_lossy<R: Read>(
    reader: R,
    mut on_line: impl FnMut(&str),
) -> std::io::Result<String> {
    let mut r = BufReader::new(reader);
    let mut buf: Vec<u8> = Vec::new();
    let mut out = String::new();
    loop {
        buf.clear();
        let n = r.read_until(b'\n', &mut buf)?;
        if n == 0 {
            break;
        }
        let cow = String::from_utf8_lossy(&buf);
        out.push_str(cow.as_ref());
        let trimmed = cow.trim();
        if !trimmed.is_empty() {
            on_line(trimmed);
        }
    }
    Ok(out)
}

fn print_verify_failure(report: &verify::VerifyReport) {
    let Some(failure) = &report.failure else {
        return;
    };

    eprintln!(
        "Verification failed: {} ({:?}, exit_code={})",
        failure.name, failure.status, failure.exit_code
    );

    if !failure.stdout.trim().is_empty() {
        eprintln!("--- stdout ---\n{}", failure.stdout.trim_end());
    }

    if !failure.stderr.trim().is_empty() {
        eprintln!("--- stderr ---\n{}", failure.stderr.trim_end());
    }
}

struct RealRunner {
    repo_root: PathBuf,
    reporter: Arc<dyn ProgressReporter>,
}

impl RealRunner {
    fn new(reporter: Arc<dyn ProgressReporter>) -> Self {
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("xtask manifest dir has a parent")
            .to_path_buf();

        Self {
            repo_root,
            reporter,
        }
    }
}

impl CommandRunner for RealRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        // Use CARGO_TERM_COLOR=never to avoid ANSI escape codes in progress lines.
        // This eliminates the need for a strip_ansi helper and keeps forwarded
        // "Compiling"/"Checking" lines readable in any terminal.
        let mut env: Vec<(&str, &str)> = spec.extra_env.to_vec();
        let color_override = ("CARGO_TERM_COLOR", "never");
        if !env.iter().any(|(k, _)| *k == "CARGO_TERM_COLOR") {
            env.push(color_override);
        }

        let mut child = Command::new(spec.program)
            .args(spec.args)
            .envs(env.iter().copied())
            .current_dir(&self.repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        let stderr_pipe = child.stderr.take().expect("stderr is piped");
        let stdout_pipe = child.stdout.take().expect("stdout is piped");

        let reporter = Arc::clone(&self.reporter);
        let check_name = spec.name.to_string();

        // Spawn a thread to read stderr and forward cargo compilation lines in real time.
        // Cargo writes "Compiling"/"Checking"/"Finished" progress to stderr.
        let stderr_thread = std::thread::spawn(move || {
            drain_reader_lines_lossy(stderr_pipe, |trimmed| {
                // Forward cargo progress lines immediately so the user sees what is
                // being compiled instead of a silent terminal during long builds.
                if trimmed.starts_with("Compiling ")
                    || trimmed.starts_with("Checking ")
                    || trimmed.starts_with("Finished ")
                    || trimmed.starts_with("Blocking ")
                {
                    reporter.check_progress(&check_name, trimmed);
                }
            })
            .unwrap_or_default()
        });

        // Read stdout on the main thread while the stderr thread drains in parallel.
        let stdout_buf = drain_reader_lines_lossy(stdout_pipe, |_| {}).unwrap_or_default();

        let stderr_buf = stderr_thread.join().unwrap_or_default();
        let status = child.wait()?;

        Ok(CommandOutput {
            exit_code: status.code().unwrap_or(1),
            stdout: stdout_buf,
            stderr: stderr_buf,
        })
    }
}

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);

    match args.next().as_deref() {
        Some("verify") => {
            // Total check count = native checks + 1 (native-scan) + all group checks.
            let total_checks = verify::NATIVE_REQUIRED_CHECKS.len()
                + 1
                + verify::FMT_CHECKS.len()
                + verify::CORE_CARGO_CHECKS.len()
                + verify::XTASK_CARGO_CHECKS.len()
                + verify::GUI_CARGO_CHECKS.len()
                + verify::FRONTEND_CHECKS.len()
                + verify::RELEASE_CHECKS.len();
            let reporter: Arc<dyn ProgressReporter> =
                Arc::new(verify::StderrProgressReporter::new(total_checks));
            let real_runner = RealRunner::new(Arc::clone(&reporter));
            let repo_root = real_runner.repo_root.clone();
            let runner = Arc::new(cache::CachingCommandRunner::new(
                real_runner,
                repo_root.clone(),
            ));
            eprintln!("=== cargo xtask verify ===");
            let verify_start = std::time::Instant::now();
            let runner_for_verify: Arc<dyn CommandRunner> = runner.clone();
            let groups = verify::CheckGroups {
                fmt: verify::FMT_CHECKS,
                core_cargo: verify::CORE_CARGO_CHECKS,
                xtask_cargo: verify::XTASK_CARGO_CHECKS,
                gui_cargo: verify::GUI_CARGO_CHECKS,
                frontend: verify::FRONTEND_CHECKS,
                release: verify::RELEASE_CHECKS,
            };
            let report = match verify::verify_fast(
                runner_for_verify,
                &repo_root,
                verify::NATIVE_REQUIRED_CHECKS,
                &groups,
                reporter.as_ref(),
            ) {
                Ok(report) => report,
                Err(err) => {
                    eprintln!("xtask error: {err:#}");
                    return ExitCode::from(1);
                }
            };
            let total_elapsed = verify_start.elapsed();

            runner.flush();

            if report.exit == VerifyExitCode::Failure {
                print_verify_failure(&report);
            }

            match report.exit {
                VerifyExitCode::Success => {
                    eprintln!("=== all {total_checks} checks passed in {total_elapsed:.1?} ===");
                    ExitCode::SUCCESS
                }
                VerifyExitCode::Failure => ExitCode::from(1),
            }
        }
        _ => {
            eprintln!("Usage: cargo xtask verify");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn test_drain_reader_lines_lossy_does_not_stop_on_invalid_utf8() {
        let bytes = b"Compiling foo v0.1.0\n\xff\xfeinvalid\nFinished\n".to_vec();
        let mut seen: Vec<String> = Vec::new();
        let out = drain_reader_lines_lossy(Cursor::new(bytes), |line| {
            seen.push(line.to_string());
        })
        .expect("drain should succeed");

        assert!(out.contains("Compiling foo"));
        assert!(out.contains("Finished"));
        assert!(
            seen.iter().any(|l| l.starts_with("Compiling ")),
            "expected Compiling line forwarded, got: {seen:?}"
        );
        assert!(
            seen.iter().any(|l| l.starts_with("Finished")),
            "expected Finished line forwarded, got: {seen:?}"
        );
        // Invalid bytes must not truncate output; lossy conversion inserts replacement chars.
        assert!(out.contains("invalid"));
    }
}
