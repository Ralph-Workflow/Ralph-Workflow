mod cache;
mod compliance;
mod scanner;
mod verify;

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};
use std::sync::Arc;

use verify::{CommandOutput, CommandRunner, CommandSpec, ProgressReporter, VerifyExitCode};

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
            let reader = BufReader::new(stderr_pipe);
            let mut buf = String::new();
            for line in reader.lines().map_while(Result::ok) {
                let trimmed = line.trim();
                // Forward cargo progress lines immediately so the user sees what is
                // being compiled instead of a silent terminal during long builds.
                if trimmed.starts_with("Compiling ")
                    || trimmed.starts_with("Checking ")
                    || trimmed.starts_with("Finished ")
                    || trimmed.starts_with("Blocking ")
                {
                    reporter.check_progress(&check_name, trimmed);
                }
                buf.push_str(&line);
                buf.push('\n');
            }
            buf
        });

        // Read stdout on the main thread while the stderr thread drains in parallel.
        let mut stdout_buf = String::new();
        for line in BufReader::new(stdout_pipe).lines().map_while(Result::ok) {
            stdout_buf.push_str(&line);
            stdout_buf.push('\n');
        }

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
            // Total check count = native checks + 1 (native-scan) + cargo checks.
            let total_checks =
                verify::NATIVE_REQUIRED_CHECKS.len() + 1 + verify::REQUIRED_CHECKS.len();
            let reporter: Arc<dyn ProgressReporter> =
                Arc::new(verify::StderrProgressReporter::new(total_checks));
            let real_runner = RealRunner::new(Arc::clone(&reporter));
            let repo_root = real_runner.repo_root.clone();
            let runner = cache::CachingCommandRunner::new(real_runner, repo_root.clone());
            eprintln!("=== cargo xtask verify ===");
            let verify_start = std::time::Instant::now();
            let report = match verify::verify_fast(
                &runner,
                &repo_root,
                verify::NATIVE_REQUIRED_CHECKS,
                verify::REQUIRED_CHECKS,
                verify::CARGO_PREFETCH_SPECS,
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
