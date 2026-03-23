// checkpoint/git_capture/io.rs — boundary module for git capture operations.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

use crate::ProcessExecutor;

pub fn git_head_oid(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["rev-parse", "HEAD"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
}

pub fn git_branch_name(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["rev-parse", "--abbrev-ref", "HEAD"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
        .filter(|branch| !branch.is_empty() && branch != "HEAD")
}

pub fn git_status(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["status", "--porcelain"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
        .filter(|status| !status.is_empty())
}

pub fn git_modified_files(executor: &dyn ProcessExecutor) -> Option<Vec<String>> {
    let diff_output = executor
        .execute("git", &["diff", "--name-only"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout)?;

    let modified_files: Vec<String> = diff_output
        .lines()
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty())
        .collect();

    (!modified_files.is_empty()).then_some(modified_files)
}
