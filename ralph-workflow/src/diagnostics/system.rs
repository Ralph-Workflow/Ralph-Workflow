//! System information gathering.

use std::process::Command;

/// System information.
#[derive(Debug, Clone)]
pub struct SystemInfo {
    pub os: String,
    pub arch: String,
    pub working_directory: Option<String>,
    pub shell: Option<String>,
    pub git_version: Option<String>,
    pub git_repo: bool,
    pub git_branch: Option<String>,
    pub uncommitted_changes: Option<usize>,
}

impl SystemInfo {
    #[must_use]
    pub fn gather() -> Self {
        Self::gather_with_runner(run_command)
    }

    pub fn gather_with_runner(runner: impl Fn(&str, &[&str]) -> Option<String>) -> Self {
        let os = std::env::consts::OS.to_string();
        let working_directory = std::env::current_dir()
            .ok()
            .map(|p| p.display().to_string());
        let shell = std::env::var("SHELL")
            .ok()
            .or_else(|| std::env::var("ComSpec").ok());

        let git_version = runner("git", &["--version"]);

        let git_repo = runner("git", &["rev-parse", "--is-inside-work-tree"])
            .is_some_and(|out| out.trim() == "true");

        let git_branch = if git_repo {
            runner("git", &["branch", "--show-current"])
        } else {
            None
        };

        let uncommitted_changes = if git_repo {
            runner("git", &["status", "--porcelain"]).map(|o| o.lines().count())
        } else {
            None
        };

        Self {
            os,
            working_directory,
            shell,
            git_version,
            git_repo,
            git_branch,
            uncommitted_changes,
            arch: std::env::consts::ARCH.to_string(),
        }
    }
}

fn run_command(command: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(command).args(args).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}
