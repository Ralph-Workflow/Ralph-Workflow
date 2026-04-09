use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;

pub use crate::runtime::verify::{CommandOutput, CommandRunner, CommandSpec};

pub struct RealRunner {
    repo_root: PathBuf,
    reporter: Arc<dyn crate::runtime::verify::ProgressReporter>,
}

impl RealRunner {
    pub fn new(reporter: Arc<dyn crate::runtime::verify::ProgressReporter>) -> Self {
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
        let env = build_command_env(spec);
        let child = spawn_command(spec, &env, &self.repo_root)?;
        capture_command_output(child, &self.reporter, spec.name)
    }
}

fn build_command_env(spec: &CommandSpec) -> Vec<(&'static str, &'static str)> {
    let color_override = ("CARGO_TERM_COLOR", "never");
    let has_color = spec.extra_env.iter().any(|(k, _)| *k == "CARGO_TERM_COLOR");
    if has_color {
        spec.extra_env.to_vec()
    } else {
        spec.extra_env
            .iter()
            .copied()
            .chain(std::iter::once(color_override))
            .collect()
    }
}

fn spawn_command(
    spec: &CommandSpec,
    env: &[(&'static str, &'static str)],
    repo_root: &PathBuf,
) -> std::io::Result<Child> {
    Command::new(spec.program)
        .args(spec.args)
        .envs(env.iter().copied())
        .current_dir(repo_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

fn capture_command_output(
    mut child: Child,
    reporter: &Arc<dyn crate::runtime::verify::ProgressReporter>,
    check_name: &'static str,
) -> std::io::Result<CommandOutput> {
    let stderr_pipe = child.stderr.take().expect("stderr is piped");
    let stdout_pipe = child.stdout.take().expect("stdout is piped");

    let stderr_thread = spawn_progress_thread(stderr_pipe, reporter, check_name);

    let stdout_buf = drain_reader_lines_lossy(stdout_pipe, |_| {}).unwrap_or_default();
    let stderr_buf = stderr_thread.join().unwrap_or_default();
    let status = child.wait()?;

    Ok(CommandOutput {
        exit_code: status.code().unwrap_or(1),
        stdout: stdout_buf,
        stderr: stderr_buf,
    })
}

fn spawn_progress_thread(
    stderr_pipe: impl Read + Send + 'static,
    reporter: &Arc<dyn crate::runtime::verify::ProgressReporter>,
    check_name: &'static str,
) -> std::thread::JoinHandle<String> {
    let reporter = Arc::clone(reporter);
    let check_name = check_name.to_string();

    std::thread::spawn(move || {
        drain_reader_lines_lossy(stderr_pipe, |trimmed| {
            // Only forward lines that are genuinely informative as real-time
            // progress. "Compiling X" and "Checking X" are suppressed: on a
            // cold cache they produce hundreds of lines per lane and flood the
            // output, while conveying nothing actionable. "Finished" and
            // "Blocking" are kept because they mark meaningful state changes.
            if trimmed.starts_with("Finished ") || trimmed.starts_with("Blocking ") {
                reporter.check_progress(&check_name, trimmed);
            }
        })
        .unwrap_or_default()
    })
}

pub fn drain_reader_lines_lossy<R: Read>(
    reader: R,
    mut on_line: impl FnMut(&str),
) -> std::io::Result<String> {
    let mut buf_reader = BufReader::new(reader);
    let mut out_parts = Vec::new();
    collect_reader_lines(&mut buf_reader, &mut out_parts, &mut on_line)?;
    Ok(out_parts.concat())
}

fn collect_reader_lines<R: Read>(
    reader: &mut BufReader<R>,
    out_parts: &mut Vec<String>,
    on_line: &mut impl FnMut(&str),
) -> std::io::Result<()> {
    while let Some(buf) = read_reader_line(reader)? {
        record_line(buf, out_parts, on_line);
    }

    Ok(())
}

fn read_reader_line<R: Read>(reader: &mut BufReader<R>) -> std::io::Result<Option<Vec<u8>>> {
    let mut buf: Vec<u8> = Vec::new();
    let n = reader.read_until(b'\n', &mut buf)?;
    if n == 0 {
        return Ok(None);
    }
    Ok(Some(buf))
}

fn record_line(buf: Vec<u8>, out_parts: &mut Vec<String>, on_line: &mut impl FnMut(&str)) {
    let cow = String::from_utf8_lossy(&buf);
    out_parts.push(cow.to_string());
    let trimmed = cow.trim();
    if !trimmed.is_empty() {
        on_line(trimmed);
    }
}
