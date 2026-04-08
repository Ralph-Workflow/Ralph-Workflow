use super::types::{FileDiff, RunChanges};
use crate::domain::run::parse_run_changes;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Get the diff of changed files for a given run.
///
/// Compares the current worktree state against its base branch.
///
/// # Errors
///
/// Returns an error if the git diff command fails or the run cannot be found.
#[tauri::command]
#[specta::specta]
pub fn get_run_changes(
    repo_path: String,
    worktree_path: Option<String>,
    iteration: Option<u32>,
) -> Result<RunChanges, String> {
    let base = worktree_path.map_or_else(|| PathBuf::from(repo_path.clone()), PathBuf::from);
    if !base.exists() {
        return Ok(RunChanges {
            files: Vec::new(),
            total_additions: 0,
            total_deletions: 0,
            iteration,
        });
    }

    let diff_output = collect_diff_output(&base);
    let domain_files = parse_run_changes(&diff_output);
    let total_additions = domain_files.iter().map(|f| f.additions).sum();
    let total_deletions = domain_files.iter().map(|f| f.deletions).sum();
    let files = domain_files
        .into_iter()
        .map(|domain| FileDiff {
            path: domain.path,
            additions: domain.additions,
            deletions: domain.deletions,
            diff_text: domain.diff_text,
        })
        .collect();

    Ok(RunChanges {
        files,
        total_additions,
        total_deletions,
        iteration,
    })
}

fn collect_diff_output(base: &Path) -> String {
    run_git_command(base, &["diff", "HEAD~1..HEAD"])
        .or_else(|| run_git_command(base, &["show", "--format=", "--unified=3"]))
        .unwrap_or_default()
}

fn run_git_command(base: &Path, args: &[&str]) -> Option<String> {
    Command::new("git")
        .args(args)
        .current_dir(base)
        .output()
        .ok()
        .filter(|out| out.status.success())
        .map(|out| String::from_utf8_lossy(&out.stdout).to_string())
}
