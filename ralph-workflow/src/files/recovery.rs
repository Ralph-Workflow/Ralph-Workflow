//! Minimal recovery mechanisms for `.agent/` state.
//!
//! Ralph uses `.agent/` as a working directory. If it contains corrupted
//! artifacts (e.g. non-UTF8 files from interrupted writes), we attempt a small
//! set of best-effort repairs so the pipeline can proceed.

use std::path::Path;

use crate::workspace::Workspace;

/// Status of a recovery operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryStatus {
    /// No recovery needed - state is valid.
    Valid,
    /// Recovery was performed successfully.
    Recovered,
    /// Recovery failed - state is unrecoverable.
    Unrecoverable(String),
}

#[derive(Debug, Clone)]
struct StateValidation {
    pub is_valid: bool,
    pub issues: Vec<String>,
}

// =============================================================================
// Workspace-based implementation (primary, for pipeline layer)
// =============================================================================

fn validate_agent_state_with_workspace(
    workspace: &dyn Workspace,
    agent_dir: &Path,
) -> StateValidation {
    let issues: Vec<String> = if !workspace.exists(agent_dir) {
        vec![".agent/ directory does not exist".to_string()]
    } else {
        let unreadable_files: Vec<String> = workspace
            .read_dir(agent_dir)
            .ok()
            .map(|entries| {
                entries
                    .iter()
                    .filter_map(|entry| {
                        let path = entry.path();
                        if entry.is_file() && workspace.read(path).is_err() {
                            Some(format!("Corrupted file: {}", path.display()))
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();

        let zero_length_files: Vec<String> = [
            "PLAN.md",
            "ISSUES.md",
            "STATUS.md",
            "NOTES.md",
            "commit-message.txt",
        ]
        .iter()
        .filter_map(|filename| {
            let file_path = agent_dir.join(filename);
            let content = workspace.read(&file_path).ok();
            if content.is_some_and(|c| c.is_empty()) {
                Some(format!("Zero-length file: {filename}"))
            } else {
                None
            }
        })
        .collect();

        unreadable_files
            .into_iter()
            .chain(zero_length_files)
            .collect()
    };

    StateValidation {
        is_valid: issues.is_empty(),
        issues,
    }
}

fn remove_zero_length_files_with_workspace(
    workspace: &dyn Workspace,
    agent_dir: &Path,
) -> std::io::Result<usize> {
    let filenames = [
        "PLAN.md",
        "ISSUES.md",
        "STATUS.md",
        "NOTES.md",
        "commit-message.txt",
    ];

    let removed: usize = filenames
        .iter()
        .filter_map(|filename| {
            let file_path = agent_dir.join(filename);
            if !workspace.exists(&file_path) {
                return None;
            }
            if let Ok(content) = workspace.read(&file_path) {
                if content.is_empty() {
                    workspace.remove(&file_path).ok()?;
                    return Some(1);
                }
            }
            None
        })
        .sum();

    Ok(removed)
}

/// Best-effort repair of common `.agent/` state issues using workspace.
///
/// This is the workspace-based version for pipeline layer usage.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn auto_repair_with_workspace(
    workspace: &dyn Workspace,
    agent_dir: &Path,
) -> std::io::Result<RecoveryStatus> {
    if !workspace.exists(agent_dir) {
        workspace.create_dir_all(&agent_dir.join("logs"))?;
        return Ok(RecoveryStatus::Recovered);
    }

    let validation = validate_agent_state_with_workspace(workspace, agent_dir);
    if validation.is_valid {
        workspace.create_dir_all(&agent_dir.join("logs"))?;
        return Ok(RecoveryStatus::Valid);
    }

    // Attempt repairs.
    remove_zero_length_files_with_workspace(workspace, agent_dir)?;
    workspace.create_dir_all(&agent_dir.join("logs"))?;

    let post_validation = validate_agent_state_with_workspace(workspace, agent_dir);
    if post_validation.is_valid {
        Ok(RecoveryStatus::Recovered)
    } else {
        Ok(RecoveryStatus::Unrecoverable(format!(
            "Unresolved .agent issues: {}",
            post_validation.issues.join(", ")
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;
    use std::path::Path;

    #[test]
    fn auto_repair_with_workspace_creates_missing_directory() {
        let workspace = MemoryWorkspace::new_test();
        let agent_dir = Path::new(".agent");

        let status = auto_repair_with_workspace(&workspace, agent_dir).unwrap();

        assert_eq!(status, RecoveryStatus::Recovered);
        assert!(workspace.exists(&agent_dir.join("logs")));
    }

    #[test]
    fn auto_repair_with_workspace_removes_zero_length_files() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/logs/.keep", "")
            .with_file(".agent/PLAN.md", ""); // Empty file

        let agent_dir = Path::new(".agent");
        let status = auto_repair_with_workspace(&workspace, agent_dir).unwrap();

        assert_eq!(status, RecoveryStatus::Recovered);
        assert!(!workspace.exists(&agent_dir.join("PLAN.md")));
    }

    #[test]
    fn auto_repair_with_workspace_valid_state() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/logs/.keep", "")
            .with_file(".agent/PLAN.md", "# Plan\nSome content");

        let agent_dir = Path::new(".agent");
        let status = auto_repair_with_workspace(&workspace, agent_dir).unwrap();

        assert_eq!(status, RecoveryStatus::Valid);
        assert!(workspace.exists(&agent_dir.join("PLAN.md")));
    }

    #[test]
    fn auto_repair_with_workspace_multiple_zero_length_files() {
        let workspace = MemoryWorkspace::new_test()
            .with_file(".agent/logs/.keep", "")
            .with_file(".agent/PLAN.md", "")
            .with_file(".agent/ISSUES.md", "")
            .with_file(".agent/STATUS.md", "valid content");

        let agent_dir = Path::new(".agent");
        let status = auto_repair_with_workspace(&workspace, agent_dir).unwrap();

        assert_eq!(status, RecoveryStatus::Recovered);
        assert!(!workspace.exists(&agent_dir.join("PLAN.md")));
        assert!(!workspace.exists(&agent_dir.join("ISSUES.md")));
        assert!(workspace.exists(&agent_dir.join("STATUS.md")));
    }
}
