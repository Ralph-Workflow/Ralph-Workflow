// diagnostics/system/io.rs — boundary module for system information gathering.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// System information gathering.

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
        let arch = std::env::consts::ARCH.to_string();
        let working_directory = gather_working_directory();
        let shell = gather_shell();
        let git_version = runner("git", &["--version"]);
        let git_repo = check_git_repo(&runner);
        let (git_branch, uncommitted_changes) = gather_git_details(&runner, git_repo);

        Self {
            os,
            arch,
            working_directory,
            shell,
            git_version,
            git_repo,
            git_branch,
            uncommitted_changes,
        }
    }
}

fn gather_working_directory() -> Option<String> {
    std::env::current_dir()
        .ok()
        .map(|p| p.display().to_string())
}

fn gather_shell() -> Option<String> {
    std::env::var("SHELL")
        .ok()
        .or_else(|| std::env::var("ComSpec").ok())
}

fn gather_git_details(
    runner: &impl Fn(&str, &[&str]) -> Option<String>,
    git_repo: bool,
) -> (Option<String>, Option<usize>) {
    let git_branch = git_repo
        .then(|| runner("git", &["branch", "--show-current"]))
        .flatten();
    let uncommitted_changes = git_repo
        .then(|| runner("git", &["status", "--porcelain"]).map(|o| o.lines().count()))
        .flatten();
    (git_branch, uncommitted_changes)
}

fn check_git_repo(runner: &impl Fn(&str, &[&str]) -> Option<String>) -> bool {
    runner("git", &["rev-parse", "--is-inside-work-tree"])
        .is_some_and(|out| out.trim() == "true")
}

fn run_command(command: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(command).args(args).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}
