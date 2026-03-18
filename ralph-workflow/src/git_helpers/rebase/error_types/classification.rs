/// Classify a Git rebase CLI error from stderr/stdout output.
///
/// Pure policy: maps output patterns to specific error kinds.
fn classify_invalid_revision(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("invalid revision")
        || output.contains("unknown revision")
        || output.contains("bad revision")
        || output.contains("ambiguous revision")
        || output.contains("not found")
        || output.contains("does not exist")
        || output.contains("no such ref")
    {
        let revision = extract_revision(output);
        Some(RebaseErrorKind::InvalidRevision {
            revision: revision.unwrap_or_else(|| "unknown".to_string()),
        })
    } else {
        None
    }
}

fn classify_shallow_or_missing_history(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("shallow")
        || output.contains("depth")
        || output.contains("unreachable")
        || output.contains("needed single revision")
        || output.contains("does not have")
    {
        Some(RebaseErrorKind::RepositoryCorrupt {
            details: format!(
                "Shallow clone or missing history: {}",
                extract_error_line(output)
            ),
        })
    } else {
        None
    }
}

fn classify_worktree_conflict(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("worktree")
        || output.contains("checked out")
        || output.contains("another branch")
        || output.contains("already checked out")
    {
        Some(RebaseErrorKind::ConcurrentOperation {
            operation: "branch checked out in another worktree".to_string(),
        })
    } else {
        None
    }
}

fn classify_submodule_conflict(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("submodule") || output.contains(".gitmodules") {
        Some(RebaseErrorKind::ContentConflict {
            files: extract_conflict_files(output),
        })
    } else {
        None
    }
}

fn classify_dirty_working_tree(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("dirty")
        || output.contains("uncommitted changes")
        || output.contains("local changes")
        || output.contains("cannot rebase")
    {
        Some(RebaseErrorKind::DirtyWorkingTree)
    } else {
        None
    }
}

fn classify_concurrent_operation(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("rebase in progress")
        || output.contains("merge in progress")
        || output.contains("cherry-pick in progress")
        || output.contains("revert in progress")
        || output.contains("bisect in progress")
        || output.contains("Another git process")
        || output.contains("Locked")
    {
        let operation = extract_operation(output);
        Some(RebaseErrorKind::ConcurrentOperation {
            operation: operation.unwrap_or_else(|| "unknown".to_string()),
        })
    } else {
        None
    }
}

fn classify_repository_corruption(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("corrupt")
        || output.contains("object not found")
        || output.contains("missing object")
        || output.contains("invalid object")
        || output.contains("bad object")
        || output.contains("disk full")
        || output.contains("filesystem")
    {
        Some(RebaseErrorKind::RepositoryCorrupt {
            details: extract_error_line(output),
        })
    } else {
        None
    }
}

fn classify_environment_failure(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("user.name")
        || output.contains("user.email")
        || output.contains("author")
        || output.contains("committer")
        || output.contains("terminal")
        || output.contains("editor")
    {
        Some(RebaseErrorKind::EnvironmentFailure {
            reason: extract_error_line(output),
        })
    } else {
        None
    }
}

fn classify_hook_rejection(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("pre-rebase") || output.contains("hook") || output.contains("rejected by") {
        Some(RebaseErrorKind::HookRejection {
            hook_name: extract_hook_name(output),
        })
    } else {
        None
    }
}

fn classify_content_conflict(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("Conflict")
        || output.contains("conflict")
        || output.contains("Resolve")
        || output.contains("Merge conflict")
    {
        Some(RebaseErrorKind::ContentConflict {
            files: extract_conflict_files(output),
        })
    } else {
        None
    }
}

fn classify_patch_failure(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("patch does not apply")
        || output.contains("patch failed")
        || output.contains("hunk failed")
        || output.contains("context mismatch")
        || output.contains("fuzz")
    {
        Some(RebaseErrorKind::PatchApplicationFailed {
            reason: extract_error_line(output),
        })
    } else {
        None
    }
}

fn classify_interactive_stop(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("Stopped at") || output.contains("paused") || output.contains("edit command")
    {
        Some(RebaseErrorKind::InteractiveStop {
            command: extract_command(output),
        })
    } else {
        None
    }
}

fn classify_empty_commit(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("empty")
        || output.contains("no changes")
        || output.contains("already applied")
    {
        Some(RebaseErrorKind::EmptyCommit)
    } else {
        None
    }
}

fn classify_autostash_failure(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("autostash") || output.contains("stash") {
        Some(RebaseErrorKind::AutostashFailed {
            reason: extract_error_line(output),
        })
    } else {
        None
    }
}

fn classify_commit_creation_failure(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("pre-commit")
        || output.contains("commit-msg")
        || output.contains("prepare-commit-msg")
        || output.contains("post-commit")
        || output.contains("signing")
        || output.contains("GPG")
    {
        Some(RebaseErrorKind::CommitCreationFailed {
            reason: extract_error_line(output),
        })
    } else {
        None
    }
}

fn classify_reference_update_failure(output: &str) -> Option<RebaseErrorKind> {
    if output.contains("cannot lock")
        || output.contains("ref update")
        || output.contains("packed-refs")
        || output.contains("reflog")
    {
        Some(RebaseErrorKind::ReferenceUpdateFailed {
            reason: extract_error_line(output),
        })
    } else {
        None
    }
}

/// Parse Git CLI output to classify rebase errors.
///
/// This function analyzes stderr/stdout from git rebase commands
/// to determine the specific failure mode.
pub fn classify_rebase_error(stderr: &str, stdout: &str) -> RebaseErrorKind {
    let output = format!("{stderr}\n{stdout}");

    classify_invalid_revision(&output)
        .or_else(|| classify_shallow_or_missing_history(&output))
        .or_else(|| classify_worktree_conflict(&output))
        .or_else(|| classify_submodule_conflict(&output))
        .or_else(|| classify_dirty_working_tree(&output))
        .or_else(|| classify_concurrent_operation(&output))
        .or_else(|| classify_repository_corruption(&output))
        .or_else(|| classify_environment_failure(&output))
        .or_else(|| classify_hook_rejection(&output))
        .or_else(|| classify_content_conflict(&output))
        .or_else(|| classify_patch_failure(&output))
        .or_else(|| classify_interactive_stop(&output))
        .or_else(|| classify_empty_commit(&output))
        .or_else(|| classify_autostash_failure(&output))
        .or_else(|| classify_commit_creation_failure(&output))
        .or_else(|| classify_reference_update_failure(&output))
        .unwrap_or_else(|| RebaseErrorKind::Unknown {
            details: extract_error_line(&output),
        })
}

/// Extract revision name from error output.
fn extract_revision(output: &str) -> Option<String> {
    // Look for patterns like "invalid revision 'foo'" or "unknown revision 'bar'"
    // Using simple string parsing instead of regex for reliability
    let patterns = [
        ("invalid revision '", "'"),
        ("unknown revision '", "'"),
        ("bad revision '", "'"),
        ("branch '", "' not found"),
        ("upstream branch '", "' not found"),
        ("revision ", " not found"),
        ("'", "'"),
    ];

    patterns.iter().find_map(|(start, end)| {
        let start_idx = output.find(start)?;
        let after_start = &output[start_idx + start.len()..];
        let end_idx = after_start.find(end)?;
        let revision = &after_start[..end_idx];
        (!revision.is_empty()).then_some(revision.to_string())
    })?;

    // Also try to extract branch names from error messages
    output
        .lines()
        .find(|line| line.contains("not found") || line.contains("does not exist"))
        .and_then(|line| {
            let words: Vec<&str> = line.split_whitespace().collect();
            words
                .iter()
                .position(|word| {
                    *word == "'" || (*word == "\"" && words.iter().take(3).any(|w| *w == "\""))
                })
                .and_then(|i| words.get(i + 1))
                .map(|w| w.to_string())
        })
}

/// Extract operation name from error output.
fn extract_operation(output: &str) -> Option<String> {
    if output.contains("rebase in progress") {
        Some("rebase".to_string())
    } else if output.contains("merge in progress") {
        Some("merge".to_string())
    } else if output.contains("cherry-pick in progress") {
        Some("cherry-pick".to_string())
    } else if output.contains("revert in progress") {
        Some("revert".to_string())
    } else if output.contains("bisect in progress") {
        Some("bisect".to_string())
    } else {
        None
    }
}

/// Extract hook name from error output.
fn extract_hook_name(output: &str) -> String {
    if output.contains("pre-rebase") {
        "pre-rebase".to_string()
    } else if output.contains("pre-commit") {
        "pre-commit".to_string()
    } else if output.contains("commit-msg") {
        "commit-msg".to_string()
    } else if output.contains("post-commit") {
        "post-commit".to_string()
    } else {
        "hook".to_string()
    }
}

/// Extract command name from error output.
fn extract_command(output: &str) -> String {
    if output.contains("edit") {
        "edit".to_string()
    } else if output.contains("reword") {
        "reword".to_string()
    } else if output.contains("break") {
        "break".to_string()
    } else if output.contains("exec") {
        "exec".to_string()
    } else {
        "unknown".to_string()
    }
}

/// Extract the first meaningful error line from output.
fn extract_error_line(output: &str) -> String {
    output
        .lines()
        .find(|line| {
            !line.is_empty()
                && !line.starts_with("hint:")
                && !line.starts_with("Hint:")
                && !line.starts_with("note:")
                && !line.starts_with("Note:")
        })
        .map_or_else(|| output.trim().to_string(), |s| s.trim().to_string())
}

/// Extract conflict file paths from error output.
fn extract_conflict_files(output: &str) -> Vec<String> {
    output
        .lines()
        .filter(|line| {
            line.contains("CONFLICT")
                || line.contains("Conflict")
                || line.contains("Merge conflict")
        })
        .filter_map(|line| {
            line.find("in ").map(|start| {
                let path = line[start + 3..].trim();
                (!path.is_empty()).then_some(path.to_string())
            })
        })
        .flatten()
        .collect()
}
