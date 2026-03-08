use serde::{Deserialize, Serialize};
use std::path::Path;

/// Current status of a Ralph run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub enum RunStatus {
    Running,
    Paused,
    Completed,
    Failed,
    NotStarted,
}

/// Detailed information about a Ralph run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunDetail {
    pub run_id: String,
    pub status: RunStatus,
    pub current_phase: String,
    pub last_checkpoint: Option<String>,
    pub agent_profile: String,
    pub repo_path: String,
    pub worktree_path: Option<String>,
    pub created_at: String,
    pub description: String,
}

/// Summary of run status for a repository/worktree context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunStatusSummary {
    pub status: RunStatus,
    pub run_id: Option<String>,
    pub current_phase: Option<String>,
    pub last_checkpoint: Option<String>,
}

/// Get the run status for a repository (and optional worktree).
///
/// Checks for active process lock file and checkpoint data.
///
/// # Errors
///
/// Returns an error if the path cannot be read.
#[tauri::command]
pub fn get_run_status(
    repo_path: String,
    worktree_path: Option<String>,
) -> Result<RunStatusSummary, String> {
    let base_path_buf = worktree_path.map_or_else(
        || std::path::PathBuf::from(repo_path),
        std::path::PathBuf::from,
    );
    let agent_dir = base_path_buf.join(".agent");

    // Check for active run lock
    let lock_file = agent_dir.join("tmp").join("run.lock");
    if lock_file.exists() {
        let checkpoint = load_checkpoint_summary(&agent_dir);
        return Ok(RunStatusSummary {
            status: RunStatus::Running,
            run_id: checkpoint.as_ref().map(|c| c.run_id.clone()),
            current_phase: checkpoint.as_ref().map(|c| c.current_phase.clone()),
            last_checkpoint: checkpoint.as_ref().and_then(|c| c.last_checkpoint.clone()),
        });
    }

    // Check for checkpoint
    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return Ok(RunStatusSummary {
            status: RunStatus::NotStarted,
            run_id: None,
            current_phase: None,
            last_checkpoint: None,
        });
    }

    let checkpoint = load_checkpoint_summary(&agent_dir);
    let status = checkpoint.as_ref().map_or(RunStatus::Paused, |c| {
        if c.current_phase == "Complete" {
            RunStatus::Completed
        } else {
            RunStatus::Paused
        }
    });

    Ok(RunStatusSummary {
        status,
        run_id: checkpoint.as_ref().map(|c| c.run_id.clone()),
        current_phase: checkpoint.as_ref().map(|c| c.current_phase.clone()),
        last_checkpoint: checkpoint.as_ref().and_then(|c| c.last_checkpoint.clone()),
    })
}

/// Internal helper: collect all resumable (paused/interrupted) runs from a list of paths.
///
/// Each path is checked for `.agent/checkpoint.json`. Completed runs are excluded.
#[must_use]
pub fn collect_resumable_runs(paths: &[std::path::PathBuf]) -> Vec<RunDetail> {
    let mut results = Vec::new();

    for repo_path in paths {
        let agent_dir = repo_path.join(".agent");
        if !agent_dir.exists() {
            continue;
        }
        let checkpoint_file = agent_dir.join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };

        let phase = checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();

        if phase == "Complete" {
            continue;
        }

        let run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();

        let timestamp = checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let developer_agent = checkpoint
            .get("developer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let reviewer_agent = checkpoint
            .get("reviewer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        results.push(RunDetail {
            run_id,
            status: RunStatus::Paused,
            current_phase: phase.clone(),
            last_checkpoint: Some(timestamp.clone()),
            agent_profile: format!("{developer_agent}/{reviewer_agent}"),
            repo_path: repo_path.to_string_lossy().into_owned(),
            worktree_path: None,
            created_at: timestamp,
            description: format!("Interrupted at {phase}"),
        });
    }

    results
}

/// Get all resumable runs across the primary repository and all known worktrees.
///
/// A run is resumable if it has a checkpoint in an interrupted/paused state.
///
/// # Errors
///
/// Returns an error if the app state lock cannot be acquired.
#[tauri::command]
pub fn get_resumable_runs(
    repo_path: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<Vec<RunDetail>, String> {
    let mut paths = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };

    let primary = std::path::PathBuf::from(&repo_path);
    if !paths.contains(&primary) {
        paths.push(primary);
    }

    Ok(collect_resumable_runs(&paths))
}

/// Internal: find a run in a set of known repos by scanning checkpoints.
fn find_run_in_repos(run_id: &str, repos: &[std::path::PathBuf]) -> Option<RunDetail> {
    for repo_path in repos {
        let checkpoint_file = repo_path.join(".agent").join("checkpoint.json");
        if !checkpoint_file.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_file) else {
            continue;
        };
        let Ok(checkpoint) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };
        let checkpoint_run_id = checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if checkpoint_run_id != run_id {
            continue;
        }
        let phase = checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();
        let timestamp = checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let developer_agent = checkpoint
            .get("developer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let reviewer_agent = checkpoint
            .get("reviewer_agent")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let status = if phase == "Complete" {
            RunStatus::Completed
        } else {
            RunStatus::Paused
        };
        return Some(RunDetail {
            run_id: run_id.to_string(),
            status,
            current_phase: phase.clone(),
            last_checkpoint: Some(timestamp.clone()),
            agent_profile: format!("{developer_agent}/{reviewer_agent}"),
            repo_path: repo_path.to_string_lossy().into_owned(),
            worktree_path: None,
            created_at: timestamp,
            description: format!("Phase: {phase}"),
        });
    }
    None
}

/// Get detailed information for a specific run by scanning all known repo paths.
///
/// # Errors
///
/// Returns an error if the run is not found in any known repository.
#[tauri::command]
pub fn get_run_detail(
    run_id: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<RunDetail, String> {
    let known_repos = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };
    find_run_in_repos(&run_id, &known_repos).ok_or_else(|| format!("Run not found: {run_id}"))
}

/// Internal struct for checkpoint summary used within this module.
struct CheckpointSummary {
    run_id: String,
    current_phase: String,
    last_checkpoint: Option<String>,
}

fn load_checkpoint_summary(agent_dir: &Path) -> Option<CheckpointSummary> {
    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return None;
    }

    let content = std::fs::read_to_string(&checkpoint_file).ok()?;
    let checkpoint: serde_json::Value = serde_json::from_str(&content).ok()?;

    Some(CheckpointSummary {
        run_id: checkpoint
            .get("run_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string(),
        current_phase: checkpoint
            .get("phase")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string(),
        last_checkpoint: checkpoint
            .get("timestamp")
            .and_then(|v| v.as_str())
            .map(String::from),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_get_run_status_returns_not_started_for_fresh_repo() {
        let dir = TempDir::new().unwrap();
        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        let status = result.unwrap();
        assert_eq!(status.status, RunStatus::NotStarted);
        assert!(status.run_id.is_none());
        assert!(status.current_phase.is_none());
        assert!(status.last_checkpoint.is_none());
    }

    #[test]
    fn test_get_run_status_returns_running_when_lock_exists() {
        let dir = TempDir::new().unwrap();
        let agent_tmp = dir.path().join(".agent").join("tmp");
        std::fs::create_dir_all(&agent_tmp).unwrap();
        std::fs::write(agent_tmp.join("run.lock"), "locked").unwrap();

        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        assert!(result.is_ok(), "Expected Ok: {result:?}");
        assert_eq!(result.unwrap().status, RunStatus::Running);
    }

    #[test]
    fn test_get_run_status_includes_phase_and_checkpoint_fields() {
        let dir = TempDir::new().unwrap();
        let result = get_run_status(dir.path().to_string_lossy().to_string(), None);
        let status = result.unwrap();
        // For NotStarted, all optional fields should be None
        assert!(status.run_id.is_none());
        assert!(status.current_phase.is_none());
        assert!(status.last_checkpoint.is_none());
    }

    #[test]
    fn test_collect_resumable_runs_returns_empty_when_no_checkpoint() {
        let dir = TempDir::new().unwrap();
        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert!(runs.is_empty());
    }

    #[test]
    fn test_collect_resumable_runs_excludes_completed_runs() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "test-run-123",
            "phase": "Complete",
            "timestamp": "2024-01-01 00:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert!(runs.is_empty(), "Completed runs should not be resumable");
    }

    #[test]
    fn test_collect_resumable_runs_includes_interrupted_runs() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "test-run-456",
            "phase": "Interrupted",
            "timestamp": "2024-01-01 12:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let runs = collect_resumable_runs(&[dir.path().to_path_buf()]);
        assert_eq!(runs.len(), 1, "Interrupted run should be resumable");
        assert_eq!(runs[0].run_id, "test-run-456");
        assert_eq!(runs[0].status, RunStatus::Paused);
    }

    #[test]
    fn test_get_run_detail_finds_run_by_id() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "target-run-id",
            "phase": "Development",
            "timestamp": "2024-06-01 10:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("target-run-id", &repos);
        assert!(detail.is_some(), "Expected Some but got None");
        let d = detail.unwrap();
        assert_eq!(d.run_id, "target-run-id");
        assert_eq!(d.current_phase, "Development");
    }

    #[test]
    fn test_get_run_detail_not_found() {
        let repos: Vec<std::path::PathBuf> = Vec::new();
        let detail = find_run_in_repos("nonexistent-run-id", &repos);
        assert!(detail.is_none());
    }

    #[test]
    fn test_get_run_detail_skips_repos_without_matching_id() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "other-run-id",
            "phase": "Development",
            "timestamp": "2024-06-01 10:00:00",
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let detail = find_run_in_repos("target-run-id", &repos);
        assert!(detail.is_none(), "Should not find unrelated run");
    }

    #[test]
    fn test_collect_resumable_runs_finds_paused_run_in_worktree() {
        // Main repo has no checkpoint (fresh)
        let main_dir = TempDir::new().unwrap();

        // Worktree has a paused checkpoint
        let worktree_dir = TempDir::new().unwrap();
        let agent_dir = worktree_dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "wt-run-789",
            "phase": "Review",
            "timestamp": "2024-06-01 09:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        // Scanning only the main repo must NOT find the worktree run
        let main_only = collect_resumable_runs(&[main_dir.path().to_path_buf()]);
        assert!(
            main_only.is_empty(),
            "Main-only scan should not find worktree run"
        );

        // Scanning both must find the worktree run
        let both = collect_resumable_runs(&[
            main_dir.path().to_path_buf(),
            worktree_dir.path().to_path_buf(),
        ]);
        assert_eq!(both.len(), 1, "Should find the paused run in the worktree");
        assert_eq!(both[0].run_id, "wt-run-789");
        assert_eq!(both[0].status, RunStatus::Paused);
    }
}
