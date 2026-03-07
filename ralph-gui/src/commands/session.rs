use serde::{Deserialize, Serialize};

/// Summary of a Ralph workflow session.
#[derive(Debug, Clone, Serialize, Deserialize)]
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
}

/// Request to create a new Ralph workflow session.
#[derive(Debug, Clone, Serialize, Deserialize)]
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
    };

    Ok(vec![summary])
}

/// Create a new session by validating the prompt file exists.
///
/// # Errors
///
/// Returns an error string if the prompt file does not exist or the repo path is invalid.
#[tauri::command]
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
    })
}

/// Get details for a specific session by `run_id`.
///
/// # Errors
///
/// Returns an error string if the `run_id` is not found.
#[tauri::command]
pub fn get_session_detail(run_id: String) -> Result<SessionSummary, String> {
    // For now, return a not-found error — a full implementation would
    // scan all repos for checkpoints matching this run_id.
    let mut msg = run_id;
    msg.insert_str(0, "Session not found: ");
    Err(msg)
}

/// Resume an interrupted session.
///
/// # Errors
///
/// Returns an error if the `run_id` is not found or is not resumable.
#[tauri::command]
pub fn resume_session(run_id: String) -> Result<SessionSummary, String> {
    let mut msg = run_id;
    msg.insert_str(0, "Session not found or not resumable: ");
    Err(msg)
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
    fn test_get_session_detail_not_found() {
        let result = get_session_detail("nonexistent-run-id".to_string());
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Session not found"));
    }

    #[test]
    fn test_resume_session_not_found() {
        let result = resume_session("nonexistent-run-id".to_string());
        assert!(result.is_err());
    }
}
