use crate::executor::ProcessExecutor;

/// Get the current git HEAD OID.
#[must_use]
pub fn git_head_oid(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["rev-parse", "HEAD"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
}

/// Get the current git branch name (short form, omits "HEAD" detached state).
#[must_use]
pub fn git_branch_name(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["rev-parse", "--abbrev-ref", "HEAD"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
        .filter(|branch| !branch.is_empty() && branch != "HEAD")
}

/// Get the git status output in porcelain format.
#[must_use]
pub fn git_status(executor: &dyn ProcessExecutor) -> Option<String> {
    executor
        .execute("git", &["status", "--porcelain"], &[], None)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| output.stdout.trim().to_string())
        .filter(|status| !status.is_empty())
}

/// Get the list of modified files from `git diff --name-only`.
#[must_use]
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

    if modified_files.is_empty() {
        None
    } else {
        Some(modified_files)
    }
}
