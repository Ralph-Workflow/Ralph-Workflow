/// Parse Git CLI output to classify rebase errors.
///
/// This function analyzes stderr/stdout from git rebase commands
/// to determine the specific failure mode.
pub fn classify_rebase_error(stderr: &str, stdout: &str) -> RebaseErrorKind {
    let combined = format!("{stderr}\n{stdout}");

    // Category 1: Rebase Cannot Start

    // Invalid revision
    if combined.contains("invalid revision")
        || combined.contains("unknown revision")
        || combined.contains("bad revision")
        || combined.contains("ambiguous revision")
        || combined.contains("not found")
        || combined.contains("does not exist")
        || combined.contains("bad revision")
        || combined.contains("no such ref")
    {
        // Try to extract the revision name
        let revision = extract_revision(&combined);
        return RebaseErrorKind::InvalidRevision {
            revision: revision.unwrap_or_else(|| "unknown".to_string()),
        };
    }

    // Shallow clone (missing history)
    if combined.contains("shallow")
        || combined.contains("depth")
        || combined.contains("unreachable")
        || combined.contains("needed single revision")
        || combined.contains("does not have")
    {
        return RebaseErrorKind::RepositoryCorrupt {
            details: format!(
                "Shallow clone or missing history: {}",
                extract_error_line(&combined)
            ),
        };
    }

    // Worktree conflict
    if combined.contains("worktree")
        || combined.contains("checked out")
        || combined.contains("another branch")
        || combined.contains("already checked out")
    {
        return RebaseErrorKind::ConcurrentOperation {
            operation: "branch checked out in another worktree".to_string(),
        };
    }

    // Submodule conflict
    if combined.contains("submodule") || combined.contains(".gitmodules") {
        return RebaseErrorKind::ContentConflict {
            files: extract_conflict_files(&combined),
        };
    }

    // Dirty working tree
    if combined.contains("dirty")
        || combined.contains("uncommitted changes")
        || combined.contains("local changes")
        || combined.contains("cannot rebase")
    {
        return RebaseErrorKind::DirtyWorkingTree;
    }

    // Concurrent operation
    if combined.contains("rebase in progress")
        || combined.contains("merge in progress")
        || combined.contains("cherry-pick in progress")
        || combined.contains("revert in progress")
        || combined.contains("bisect in progress")
        || combined.contains("Another git process")
        || combined.contains("Locked")
    {
        let operation = extract_operation(&combined);
        return RebaseErrorKind::ConcurrentOperation {
            operation: operation.unwrap_or_else(|| "unknown".to_string()),
        };
    }

    // Repository corruption
    if combined.contains("corrupt")
        || combined.contains("object not found")
        || combined.contains("missing object")
        || combined.contains("invalid object")
        || combined.contains("bad object")
        || combined.contains("disk full")
        || combined.contains("filesystem")
    {
        return RebaseErrorKind::RepositoryCorrupt {
            details: extract_error_line(&combined),
        };
    }

    // Environment failure
    if combined.contains("user.name")
        || combined.contains("user.email")
        || combined.contains("author")
        || combined.contains("committer")
        || combined.contains("terminal")
        || combined.contains("editor")
    {
        return RebaseErrorKind::EnvironmentFailure {
            reason: extract_error_line(&combined),
        };
    }

    // Hook rejection
    if combined.contains("pre-rebase")
        || combined.contains("hook")
        || combined.contains("rejected by")
    {
        return RebaseErrorKind::HookRejection {
            hook_name: extract_hook_name(&combined),
        };
    }

    // Category 2: Rebase Stops (Interrupted)

    // Content conflicts
    if combined.contains("Conflict")
        || combined.contains("conflict")
        || combined.contains("Resolve")
        || combined.contains("Merge conflict")
    {
        return RebaseErrorKind::ContentConflict {
            files: extract_conflict_files(&combined),
        };
    }

    // Patch application failure
    if combined.contains("patch does not apply")
        || combined.contains("patch failed")
        || combined.contains("hunk failed")
        || combined.contains("context mismatch")
        || combined.contains("fuzz")
    {
        return RebaseErrorKind::PatchApplicationFailed {
            reason: extract_error_line(&combined),
        };
    }

    // Interactive stop
    if combined.contains("Stopped at")
        || combined.contains("paused")
        || combined.contains("edit command")
    {
        return RebaseErrorKind::InteractiveStop {
            command: extract_command(&combined),
        };
    }

    // Empty commit
    if combined.contains("empty")
        || combined.contains("no changes")
        || combined.contains("already applied")
    {
        return RebaseErrorKind::EmptyCommit;
    }

    // Autostash failure
    if combined.contains("autostash") || combined.contains("stash") {
        return RebaseErrorKind::AutostashFailed {
            reason: extract_error_line(&combined),
        };
    }

    // Commit creation failure
    if combined.contains("pre-commit")
        || combined.contains("commit-msg")
        || combined.contains("prepare-commit-msg")
        || combined.contains("post-commit")
        || combined.contains("signing")
        || combined.contains("GPG")
    {
        return RebaseErrorKind::CommitCreationFailed {
            reason: extract_error_line(&combined),
        };
    }

    // Reference update failure
    if combined.contains("cannot lock")
        || combined.contains("ref update")
        || combined.contains("packed-refs")
        || combined.contains("reflog")
    {
        return RebaseErrorKind::ReferenceUpdateFailed {
            reason: extract_error_line(&combined),
        };
    }

    // Category 5: Unknown
    RebaseErrorKind::Unknown {
        details: extract_error_line(&combined),
    }
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
