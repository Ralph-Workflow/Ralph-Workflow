mod compliance;
mod verify;

use std::path::PathBuf;
use std::process::{Command, ExitCode};

use verify::{CommandOutput, CommandRunner, CommandSpec, VerifyExitCode};

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
}

impl RealRunner {
    fn new() -> Self {
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("xtask manifest dir has a parent")
            .to_path_buf();

        Self { repo_root }
    }
}

impl CommandRunner for RealRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let output = Command::new(spec.program)
            .args(spec.args)
            .current_dir(&self.repo_root)
            .output()?;

        Ok(CommandOutput {
            exit_code: output.status.code().unwrap_or(1),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
    }
}

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);

    match args.next().as_deref() {
        Some("verify") => {
            let runner = RealRunner::new();
            let repo_root = runner.repo_root.clone();
            let report = match verify::verify(&runner, &repo_root) {
                Ok(report) => report,
                Err(err) => {
                    eprintln!("xtask error: {err:#}");
                    return ExitCode::from(1);
                }
            };

            if report.exit == VerifyExitCode::Failure {
                print_verify_failure(&report);
            }

            match report.exit {
                VerifyExitCode::Success => ExitCode::SUCCESS,
                VerifyExitCode::Failure => ExitCode::from(1),
            }
        }
        _ => {
            eprintln!("Usage: cargo xtask verify");
            ExitCode::from(2)
        }
    }
}
