use serde::{Deserialize, Serialize};
use specta::Type;

/// Summary of a Ralph workflow session.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct SessionSummary {
    pub run_id: String,
    pub status: String,
    pub repo_path: String,
    pub worktree_path: Option<String>,
    pub created_at: String,
    pub description: String,
    pub developer_agent: String,
    pub reviewer_agent: String,
    pub phase: String,
    /// True when the run is operating with degraded conditions (retries exceeded,
    /// fallback agents used, etc.). Defaults to false for older checkpoints.
    #[serde(default)]
    pub is_degraded: bool,
}

/// Request to create a new Ralph workflow session.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct CreateSessionRequest {
    pub repo_path: String,
    pub worktree_path: Option<String>,
    pub prompt_path: String,
    pub developer_iterations: u32,
    pub reviewer_passes: u32,
}

/// List sessions for a given repository by scanning checkpoint files.
///
/// # Errors
///
/// Returns an error string if the repo path is invalid or cannot be read.
#[tauri::command]
#[specta::specta]
pub fn get_sessions(repo_path: String) -> Result<Vec<SessionSummary>, String> {
    let repo_path = std::path::PathBuf::from(repo_path);
    let repo = repo_path.as_path();
    if !repo.exists() {
        return Err(format!(
            "Repository path does not exist: {}",
            repo_path.display()
        ));
    }

    let agent_dir = repo.join(".agent");
    if !agent_dir.exists() {
        // No .agent directory means no sessions — return empty list
        return Ok(Vec::new());
    }

    let checkpoint_path = agent_dir.join("checkpoint.json");
    if !checkpoint_path.exists() {
        return Ok(Vec::new());
    }

    let content = std::fs::read_to_string(&checkpoint_path)
        .map_err(|e| format!("Failed to read checkpoint: {e}"))?;

    let checkpoint: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| format!("Failed to parse checkpoint: {e}"))?;

    let run_id = checkpoint
        .get("run_id")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

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
        "completed"
    } else if phase == "Interrupted" {
        "interrupted"
    } else {
        "paused"
    }
    .to_string();

    let is_degraded = checkpoint
        .get("is_degraded")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);

    let summary = SessionSummary {
        run_id,
        status,
        repo_path: repo_path.to_string_lossy().into_owned(),
        worktree_path: None,
        created_at: timestamp,
        description: phase.clone(),
        developer_agent,
        reviewer_agent,
        phase,
        is_degraded,
    };

    Ok(vec![summary])
}

/// Create a new session by validating the prompt file exists.
///
/// # Errors
///
/// Returns an error string if the prompt file does not exist or the repo path is invalid.
#[tauri::command]
#[specta::specta]
pub fn create_session(request: CreateSessionRequest) -> Result<SessionSummary, String> {
    let prompt_path = std::path::PathBuf::from(&request.prompt_path);
    if !prompt_path.exists() {
        return Err(format!(
            "Prompt file does not exist: {}",
            request.prompt_path
        ));
    }

    let repo_path = std::path::PathBuf::from(&request.repo_path);
    if !repo_path.exists() {
        return Err(format!(
            "Repository path does not exist: {}",
            request.repo_path
        ));
    }

    // Validate it's a git repo
    git2::Repository::open(&repo_path).map_err(|e| format!("Not a valid git repository: {e}"))?;

    let run_id = uuid::Uuid::new_v4().to_string();
    let created_at = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();

    Ok(SessionSummary {
        run_id,
        status: "pending".to_string(),
        repo_path: request.repo_path,
        worktree_path: request.worktree_path,
        created_at,
        description: format!(
            "New session ({} dev iters, {} reviewer passes)",
            request.developer_iterations, request.reviewer_passes
        ),
        developer_agent: String::new(),
        reviewer_agent: String::new(),
        phase: "Pending".to_string(),
        is_degraded: false,
    })
}

/// Internal: search known repo paths for a session checkpoint matching `run_id`.
fn find_session_in_repos(run_id: &str, repos: &[std::path::PathBuf]) -> Option<SessionSummary> {
    for repo_path in repos {
        let checkpoint_path = repo_path.join(".agent").join("checkpoint.json");
        if !checkpoint_path.exists() {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&checkpoint_path) else {
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
            "completed"
        } else {
            "paused"
        }
        .to_string();
        let is_degraded = checkpoint
            .get("is_degraded")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        return Some(SessionSummary {
            run_id: run_id.to_string(),
            status,
            repo_path: repo_path.to_string_lossy().into_owned(),
            worktree_path: None,
            created_at: timestamp,
            description: phase.clone(),
            developer_agent,
            reviewer_agent,
            phase,
            is_degraded,
        });
    }
    None
}

/// Get details for a specific session by `run_id`.
///
/// Scans all known repository paths for a checkpoint matching the given `run_id`.
///
/// # Errors
///
/// Returns an error string if the `run_id` is not found in any known repository.
#[tauri::command]
#[specta::specta]
pub fn get_session_detail(
    run_id: String,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<SessionSummary, String> {
    let known_repos = {
        let locked = state
            .lock()
            .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
        locked.known_repos.clone()
    };
    find_session_in_repos(&run_id, &known_repos)
        .ok_or_else(|| format!("Session not found: {run_id}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_get_sessions_returns_empty_for_fresh_repo() {
        let dir = TempDir::new().unwrap();
        let result = get_sessions(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_get_sessions_returns_empty_when_no_checkpoint() {
        let dir = TempDir::new().unwrap();
        // Create .agent dir but no checkpoint file
        std::fs::create_dir(dir.path().join(".agent")).unwrap();
        let result = get_sessions(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        assert!(result.unwrap().is_empty());
    }

    #[test]
    fn test_get_sessions_errors_for_nonexistent_path() {
        let result = get_sessions("/nonexistent/path/that/does/not/exist".to_string());
        assert!(result.is_err());
    }

    #[test]
    fn test_create_session_errors_when_prompt_missing() {
        let dir = TempDir::new().unwrap();
        // Initialize git repo
        git2::Repository::init(dir.path()).unwrap();
        let request = CreateSessionRequest {
            repo_path: dir.path().to_string_lossy().to_string(),
            worktree_path: None,
            prompt_path: dir
                .path()
                .join("NONEXISTENT.md")
                .to_string_lossy()
                .to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
        };
        let result = create_session(request);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Prompt file does not exist"));
    }

    #[test]
    fn test_create_session_errors_when_repo_not_git() {
        let dir = TempDir::new().unwrap();
        // Create prompt file but don't init git repo
        let prompt = dir.path().join("PROMPT.md");
        std::fs::write(&prompt, "# Test prompt").unwrap();
        let request = CreateSessionRequest {
            repo_path: dir.path().to_string_lossy().to_string(),
            worktree_path: None,
            prompt_path: prompt.to_string_lossy().to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
        };
        let result = create_session(request);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Not a valid git repository"));
    }

    #[test]
    fn test_create_session_succeeds_with_valid_inputs() {
        let dir = TempDir::new().unwrap();
        git2::Repository::init(dir.path()).unwrap();
        let prompt = dir.path().join("PROMPT.md");
        std::fs::write(&prompt, "# Test prompt\n\nThis is a test.").unwrap();
        let request = CreateSessionRequest {
            repo_path: dir.path().to_string_lossy().to_string(),
            worktree_path: None,
            prompt_path: prompt.to_string_lossy().to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
        };
        let result = create_session(request);
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        let summary = result.unwrap();
        assert!(!summary.run_id.is_empty());
        assert_eq!(summary.status, "pending");
    }

    #[test]
    fn test_get_sessions_status_derives_from_checkpoint_phase() {
        // Complete phase → "completed"
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();

        for (phase, expected_status) in &[
            ("Complete", "completed"),
            ("Interrupted", "interrupted"),
            ("Review", "paused"),
        ] {
            let checkpoint = serde_json::json!({
                "run_id": format!("run-{phase}"),
                "phase": phase,
                "timestamp": "2024-06-01 09:00:00",
                "developer_agent": "claude",
                "reviewer_agent": "openai"
            });
            std::fs::write(
                agent_dir.join("checkpoint.json"),
                serde_json::to_string(&checkpoint).unwrap(),
            )
            .unwrap();

            let result = get_sessions(dir.path().to_string_lossy().to_string());
            assert!(result.is_ok(), "Expected Ok for phase {phase}");
            let sessions = result.unwrap();
            assert_eq!(sessions.len(), 1, "Expected one session for phase {phase}");
            assert_eq!(
                sessions[0].status, *expected_status,
                "Phase '{phase}' should map to status '{expected_status}'"
            );
        }
    }

    #[test]
    fn test_session_summary_is_degraded_field_serializes_correctly() {
        let summary = SessionSummary {
            run_id: "test-run".to_string(),
            status: "paused".to_string(),
            repo_path: "/repo".to_string(),
            worktree_path: None,
            created_at: "2024-01-01".to_string(),
            description: "test".to_string(),
            developer_agent: "claude".to_string(),
            reviewer_agent: "codex".to_string(),
            phase: "Review".to_string(),
            is_degraded: true,
        };
        let value = serde_json::to_value(&summary).expect("serialization failed");
        assert_eq!(
            value
                .get("is_degraded")
                .and_then(serde_json::Value::as_bool),
            Some(true),
            "is_degraded: true should serialize as JSON true"
        );

        let summary_false = SessionSummary {
            is_degraded: false,
            ..summary
        };
        let value_false = serde_json::to_value(&summary_false).expect("serialization failed");
        assert_eq!(
            value_false
                .get("is_degraded")
                .and_then(serde_json::Value::as_bool),
            Some(false),
            "is_degraded: false should serialize as JSON false"
        );
    }

    #[test]
    fn test_get_sessions_reads_is_degraded_from_checkpoint() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "degraded-run",
            "phase": "Review",
            "timestamp": "2024-06-01 09:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "codex",
            "is_degraded": true
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let result = get_sessions(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        let sessions = result.unwrap();
        assert_eq!(sessions.len(), 1);
        assert!(
            sessions[0].is_degraded,
            "is_degraded should be true from checkpoint"
        );
    }

    #[test]
    fn test_get_sessions_defaults_is_degraded_to_false_when_missing() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "normal-run",
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

        let result = get_sessions(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        let sessions = result.unwrap();
        assert_eq!(sessions.len(), 1);
        assert!(
            !sessions[0].is_degraded,
            "is_degraded should default to false"
        );
    }

    #[test]
    fn test_get_session_detail_not_found_in_empty_repos() {
        let repos: Vec<std::path::PathBuf> = Vec::new();
        let result = find_session_in_repos("nonexistent-run-id", &repos);
        assert!(result.is_none());
    }

    #[test]
    fn test_get_session_detail_finds_by_run_id() {
        let dir = TempDir::new().unwrap();
        let agent_dir = dir.path().join(".agent");
        std::fs::create_dir(&agent_dir).unwrap();
        let checkpoint = serde_json::json!({
            "run_id": "session-xyz",
            "phase": "Review",
            "timestamp": "2024-06-01 09:00:00",
            "developer_agent": "claude",
            "reviewer_agent": "openai"
        });
        std::fs::write(
            agent_dir.join("checkpoint.json"),
            serde_json::to_string(&checkpoint).unwrap(),
        )
        .unwrap();

        let repos = vec![dir.path().to_path_buf()];
        let result = find_session_in_repos("session-xyz", &repos);
        assert!(result.is_some(), "Expected Some but got None");
        let summary = result.unwrap();
        assert_eq!(summary.run_id, "session-xyz");
        assert_eq!(summary.phase, "Review");
    }
}
