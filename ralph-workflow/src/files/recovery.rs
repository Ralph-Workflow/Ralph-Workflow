//! Minimal recovery mechanisms for `.agent/` state.
//!
//! Ralph uses `.agent/` as a working directory. If it contains corrupted
//! artifacts (e.g. non-UTF8 files from interrupted writes), we attempt a small
//! set of best-effort repairs so the pipeline can proceed.

use std::io;
use std::path::Path;

use crate::workspace::Workspace;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryStatus {
    Valid,
    Recovered,
    Unrecoverable(String),
}

#[derive(Debug, Clone)]
struct StateValidation {
    is_valid: bool,
    issues: Vec<String>,
}

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

        unreadable_files
    };

    StateValidation {
        is_valid: issues.is_empty(),
        issues,
    }
}

pub fn auto_repair_with_workspace(
    workspace: &dyn Workspace,
    agent_dir: &Path,
) -> io::Result<RecoveryStatus> {
    let validation = validate_agent_state_with_workspace(workspace, agent_dir);

    if validation.is_valid {
        return Ok(RecoveryStatus::Valid);
    }

    for issue in &validation.issues {
        eprintln!(".agent/ state issue: {issue}");
    }

    if !workspace.exists(agent_dir) {
        workspace.create_dir_all(agent_dir)?;
        return Ok(RecoveryStatus::Recovered);
    }

    let lock_files: Vec<_> = workspace
        .read_dir(agent_dir)
        .ok()
        .map(|entries| {
            entries
                .iter()
                .filter(|entry| {
                    let name = entry.file_name().to_str().unwrap_or("");
                    name.ends_with(".lock")
                })
                .filter_map(|entry| entry.path().file_name().map(|n| n.to_owned()))
                .collect()
        })
        .unwrap_or_default();

    for lock_file in lock_files {
        let lock_path = agent_dir.join(&lock_file);
        let _ = workspace.remove(&lock_path);
    }

    Ok(RecoveryStatus::Recovered)
}
