// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]

use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Arc;

pub use crate::verify::{CommandOutput, CommandRunner, CommandSpec};

pub struct RealRunner {
    repo_root: PathBuf,
    reporter: Arc<dyn crate::verify::ProgressReporter>,
}

impl RealRunner {
    pub fn new(reporter: Arc<dyn crate::verify::ProgressReporter>) -> Self {
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("xtask manifest dir has a parent")
            .to_path_buf();

        Self {
            repo_root,
            reporter,
        }
    }

    pub fn repo_root(&self) -> &PathBuf {
        &self.repo_root
    }
}

impl CommandRunner for RealRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let color_override = ("CARGO_TERM_COLOR", "never");
        let has_color = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TERM_COLOR");
        let env: Vec<(&str, &str)> = if has_color {
            spec.extra_env.to_vec()
        } else {
            spec.extra_env
                .iter()
                .copied()
                .chain(std::iter::once(color_override))
                .collect()
        };

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

        let stderr_thread = std::thread::spawn(move || {
            drain_reader_lines_lossy(stderr_pipe, |trimmed| {
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

pub fn drain_reader_lines_lossy<R: Read>(
    reader: R,
    mut on_line: impl FnMut(&str),
) -> std::io::Result<String> {
    let mut r = BufReader::new(reader);
    let mut out_parts: Vec<String> = Vec::new();
    loop {
        let mut buf: Vec<u8> = Vec::new();
        let n = r.read_until(b'\n', &mut buf)?;
        if n == 0 {
            break;
        }
        let cow = String::from_utf8_lossy(&buf);
        out_parts.push(cow.to_string());
        let trimmed = cow.trim();
        if !trimmed.is_empty() {
            on_line(trimmed);
        }
    }
    Ok(out_parts.concat())
}
