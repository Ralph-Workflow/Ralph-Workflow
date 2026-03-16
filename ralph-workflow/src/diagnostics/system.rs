//! System information gathering.

use crate::executor::{ProcessExecutor, RealProcessExecutor};

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
    /// Gather system information using default process executor.
    #[must_use]
    pub fn gather() -> Self {
        Self::gather_with_executor(&RealProcessExecutor)
    }

    /// Gather system information with a provided process executor.
    pub fn gather_with_executor(executor: &dyn ProcessExecutor) -> Self {
        use crate::diagnostics::runtime as rt;
        let os = rt::get_os_info();
        let working_directory = rt::get_working_directory()
            .ok()
            .map(|p| p.display().to_string());
        let shell = rt::get_shell();

        let git_version = executor
            .execute("git", &["--version"], &[], None)
            .ok()
            .map(|o| o.stdout.trim().to_string());

        let git_repo = executor
            .execute("git", &["rev-parse", "--git-dir"], &[], None)
            .map(|o| o.status.success())
            .unwrap_or(false);

        let git_branch = if git_repo {
            executor
                .execute("git", &["branch", "--show-current"], &[], None)
                .ok()
                .map(|o| o.stdout.trim().to_string())
        } else {
            None
        };

        let uncommitted_changes = if git_repo {
            executor
                .execute("git", &["status", "--porcelain"], &[], None)
                .ok()
                .map(|o| o.stdout.lines().count())
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
            arch: rt::get_arch().to_string(),
        }
    }
}
