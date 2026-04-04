use crate::commands::run_management::checkpoint_boundary;
use crate::commands::run_management::helpers::status as status_helper;
use crate::domain::run::{checkpoint_matches_run_id, RunDetail, RunStatus, RunStatusSummary};
use crate::state::SharedState;
use std::path::PathBuf;

fn known_repos(state: &SharedState) -> Result<Vec<PathBuf>, String> {
    let locked = state
        .lock()
        .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
    Ok(locked.known_repos.clone())
}

fn read_checkpoint_for_repo(repo_path: &PathBuf) -> Option<serde_json::Value> {
    let agent_dir = repo_path.join(".agent");
    checkpoint_boundary::read_checkpoint(&agent_dir)
}

/// Get the run status for a repository (and optional worktree).
///
/// Checks for active process lock file and checkpoint data.
///
/// # Errors
///
/// Returns an error if the path cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_run_status(
    repo_path: String,
    worktree_path: Option<String>,
) -> Result<RunStatusSummary, String> {
    let base_path_buf =
        worktree_path.map_or_else(|| PathBuf::from(repo_path.clone()), PathBuf::from);
    let agent_dir = base_path_buf.join(".agent");
    let lock_file = agent_dir.join("tmp").join("run.lock");
    let lock_present = lock_file.exists();

    let checkpoint_summary =
        crate::commands::run_management::checkpoint::load_checkpoint_summary(&agent_dir);

    Ok(status_helper::build_status_summary(
        lock_present,
        checkpoint_summary,
    ))
}

/// Get all resumable runs across the primary repository and all known worktrees.
///
/// A run is resumable if it has a checkpoint in an interrupted/paused state.
///
/// # Errors
///
/// Returns an error if the app state lock cannot be acquired.
#[tauri::command]
#[specta::specta]
pub fn get_resumable_runs(
    repo_path: String,
    state: tauri::State<'_, SharedState>,
) -> Result<Vec<RunDetail>, String> {
    let mut paths = known_repos(state.inner())?;
    let primary = PathBuf::from(repo_path);
    if !paths.contains(&primary) {
        paths.push(primary);
    }

    let results = paths
        .iter()
        .filter_map(|repo| read_checkpoint_for_repo(repo).map(|checkpoint| (repo, checkpoint)))
        .filter_map(|(repo, checkpoint)| {
            let phase = checkpoint
                .get("phase")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown");
            if phase == "Complete" {
                return None;
            }

            let run_id = checkpoint
                .get("run_id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            Some(status_helper::run_detail_from_checkpoint(
                run_id,
                &checkpoint,
                repo,
                RunStatus::Paused,
                format!("Interrupted at {phase}"),
            ))
        })
        .collect();

    Ok(results)
}

/// Get detailed information for a specific run by scanning all known repo paths.
///
/// # Errors
///
/// Returns an error if the run is not found in any known repository.
#[tauri::command]
#[specta::specta]
pub fn get_run_detail(
    run_id: String,
    state: tauri::State<'_, SharedState>,
) -> Result<RunDetail, String> {
    let repos = known_repos(state.inner())?;
    repos
        .iter()
        .filter_map(|repo| read_checkpoint_for_repo(repo).map(|checkpoint| (repo, checkpoint)))
        .find_map(|(repo, checkpoint)| {
            if !checkpoint_matches_run_id(&checkpoint, &run_id) {
                return None;
            }

            let phase = checkpoint
                .get("phase")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown")
                .to_string();
            let status = if phase == "Complete" {
                RunStatus::Completed
            } else {
                RunStatus::Paused
            };

            Some(status_helper::run_detail_from_checkpoint(
                run_id.clone(),
                &checkpoint,
                repo,
                status,
                format!("Phase: {phase}"),
            ))
        })
        .ok_or_else(|| format!("Run not found: {run_id}"))
}
