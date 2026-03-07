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

/// Get all resumable runs in a repository.
///
/// A run is resumable if it has a checkpoint in an interrupted/paused state.
///
/// # Errors
///
/// Returns an error if the path cannot be read.
#[tauri::command]
pub fn get_resumable_runs(repo_path: String) -> Result<Vec<RunDetail>, String> {
    let repo_path_buf = std::path::PathBuf::from(repo_path);
    let agent_dir = repo_path_buf.join(".agent");
    if !agent_dir.exists() {
        return Ok(Vec::new());
    }

    let checkpoint_file = agent_dir.join("checkpoint.json");
    if !checkpoint_file.exists() {
        return Ok(Vec::new());
    }

    let content = std::fs::read_to_string(&checkpoint_file)
        .map_err(|e| format!("Failed to read checkpoint: {e}"))?;

    let checkpoint: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| format!("Failed to parse checkpoint: {e}"))?;

    let phase = checkpoint
        .get("phase")
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown")
        .to_string();

    // Only return paused/interrupted runs as resumable
    if phase == "Complete" {
        return Ok(Vec::new());
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

    let detail = RunDetail {
        run_id,
        status: RunStatus::Paused,
        current_phase: phase.clone(),
        last_checkpoint: Some(timestamp.clone()),
        agent_profile: format!("{developer_agent}/{reviewer_agent}"),
        repo_path: repo_path_buf.to_string_lossy().into_owned(),
        worktree_path: None,
        created_at: timestamp,
        description: format!("Interrupted at {phase}"),
    };

    Ok(vec![detail])
}

/// Get detailed information for a specific run.
///
/// # Errors
///
/// Returns an error if the run is not found.
#[tauri::command]
pub fn get_run_detail(run_id: String) -> Result<RunDetail, String> {
    let mut msg = run_id;
    msg.insert_str(0, "Run not found: ");
    Err(msg)
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
    fn test_get_resumable_runs_returns_empty_when_no_checkpoint() {
        let dir = TempDir::new().unwrap();
        let result = get_resumable_runs(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_get_resumable_runs_excludes_completed_runs() {
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

        let result = get_resumable_runs(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        assert!(
            result.unwrap().is_empty(),
            "Completed runs should not be resumable"
        );
    }

    #[test]
    fn test_get_resumable_runs_includes_interrupted_runs() {
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

        let result = get_resumable_runs(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        let runs = result.unwrap();
        assert_eq!(runs.len(), 1, "Interrupted run should be resumable");
        assert_eq!(runs[0].run_id, "test-run-456");
        assert_eq!(runs[0].status, RunStatus::Paused);
    }

    #[test]
    fn test_get_run_detail_not_found() {
        let result = get_run_detail("nonexistent-run-id".to_string());
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Run not found"));
    }
}
