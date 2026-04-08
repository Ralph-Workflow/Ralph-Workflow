use serde::{Deserialize, Serialize};
use specta::Type;

/// Find the `ralph` binary on the system PATH.
///
/// # Errors
///
/// Returns an error string if `ralph` is not found.
fn find_ralph_binary() -> Result<std::path::PathBuf, String> {
    which::which("ralph").map_err(|_| {
        "ralph binary not found in PATH. Install it with: cargo install ralph-workflow".to_string()
    })
}

#[path = "runtime/session_launch_runtime.rs"]
mod runtime;

/// Arguments for launching a new unattended Ralph session.
#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct LaunchSessionArgs {
    pub repo_path: String,
    pub worktree_path: Option<String>,
    pub prompt_path: String,
    pub developer_iterations: u32,
    pub reviewer_passes: u32,
    pub developer_agent: Option<String>,
    pub reviewer_agent: Option<String>,
}

/// Launch a ralph session as a background process.
///
/// When `RALPH_GUI_DRY_RUN=1` is set, the binary is not spawned (useful for tests).
///
/// # Errors
///
/// Returns an error if the ralph binary is not found, the paths are invalid,
/// or the process cannot be spawned.
#[tauri::command]
#[specta::specta]
pub fn launch_ralph_session(args: LaunchSessionArgs) -> Result<String, String> {
    let target_path = args.worktree_path.as_deref().unwrap_or(&args.repo_path);

    let target_pb = std::path::PathBuf::from(target_path);
    if !target_pb.exists() {
        return Err(format!("Path does not exist: {target_path}"));
    }

    let prompt_pb = std::path::PathBuf::from(&args.prompt_path);
    if !prompt_pb.exists() {
        return Err(format!("Prompt file does not exist: {}", args.prompt_path));
    }

    // Dry-run mode for tests — skip spawning.
    if runtime::is_dry_run_enabled() {
        let run_id = uuid::Uuid::new_v4().to_string();
        return Ok(run_id);
    }

    let ralph = find_ralph_binary()?;
    let pid = runtime::spawn_launch_process(
        &ralph,
        &target_pb,
        &args.prompt_path,
        args.developer_iterations,
        args.reviewer_passes,
        args.developer_agent.as_deref(),
        args.reviewer_agent.as_deref(),
    )?;
    runtime::store_pid_file(&target_pb, pid);

    // Generate a session run_id for the GUI to track this launch.
    let run_id = uuid::Uuid::new_v4().to_string();
    Ok(run_id)
}

/// Resume an interrupted Ralph session identified by `run_id`.
///
/// When `RALPH_GUI_DRY_RUN=1` is set, the binary is not spawned (useful for tests).
///
/// # Errors
///
/// Returns an error if the binary is not found or the process cannot be spawned.
#[tauri::command]
#[specta::specta]
pub fn resume_ralph_session(run_id: String, repo_path: String) -> Result<(), String> {
    if run_id.is_empty() {
        return Err("run_id must not be empty".to_string());
    }

    let target_pb = std::path::PathBuf::from(&repo_path);
    if !target_pb.exists() {
        return Err(format!("Repository path does not exist: {repo_path}"));
    }

    // Dry-run mode for tests.
    if runtime::is_dry_run_enabled() {
        return Ok(());
    }

    let ralph = find_ralph_binary()?;

    runtime::spawn_resume_process(&ralph, &target_pb)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn make_git_repo_with_prompt() -> TempDir {
        let dir = TempDir::new().unwrap();
        git2::Repository::init(dir.path()).unwrap();
        std::fs::write(dir.path().join("PROMPT.md"), "# Test\n\nDo stuff.").unwrap();
        dir
    }

    #[test]
    fn test_launch_ralph_session_dry_run_succeeds() {
        let dir = make_git_repo_with_prompt();
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = launch_ralph_session(LaunchSessionArgs {
            repo_path: dir.path().to_string_lossy().to_string(),
            worktree_path: None,
            prompt_path: dir.path().join("PROMPT.md").to_string_lossy().to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
            developer_agent: None,
            reviewer_agent: None,
        });
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        assert!(result.is_ok(), "Expected Ok in dry-run: {result:?}");
        let run_id = result.unwrap();
        assert!(!run_id.is_empty(), "Should return a run_id");
    }

    #[test]
    fn test_launch_ralph_session_errors_when_repo_path_missing() {
        // No RALPH_GUI_DRY_RUN needed — path existence check happens before dry-run guard.
        let result = launch_ralph_session(LaunchSessionArgs {
            repo_path: "/nonexistent/repo/path/that/does/not/exist".to_string(),
            worktree_path: None,
            prompt_path: "/nonexistent/PROMPT.md".to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
            developer_agent: None,
            reviewer_agent: None,
        });
        assert!(result.is_err(), "Expected error for missing repo path");
        assert!(
            result.unwrap_err().contains("does not exist"),
            "Error should mention path does not exist"
        );
    }

    #[test]
    fn test_launch_ralph_session_errors_when_prompt_missing() {
        let dir = TempDir::new().unwrap();
        git2::Repository::init(dir.path()).unwrap();
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = launch_ralph_session(LaunchSessionArgs {
            repo_path: dir.path().to_string_lossy().to_string(),
            worktree_path: None,
            prompt_path: dir
                .path()
                .join("NONEXISTENT.md")
                .to_string_lossy()
                .to_string(),
            developer_iterations: 3,
            reviewer_passes: 2,
            developer_agent: None,
            reviewer_agent: None,
        });
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Prompt file does not exist"));
    }

    #[test]
    fn test_resume_ralph_session_dry_run_succeeds() {
        let dir = TempDir::new().unwrap();
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = resume_ralph_session(
            "test-run-id-123".to_string(),
            dir.path().to_string_lossy().to_string(),
        );
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        assert!(result.is_ok(), "Expected Ok in dry-run: {result:?}");
    }

    #[test]
    fn test_resume_ralph_session_errors_with_empty_run_id() {
        let dir = TempDir::new().unwrap();
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = resume_ralph_session(String::new(), dir.path().to_string_lossy().to_string());
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("run_id must not be empty"));
    }

    #[test]
    fn test_resume_ralph_session_errors_when_repo_missing() {
        std::env::set_var("RALPH_GUI_DRY_RUN", "1");
        let result = resume_ralph_session(
            "some-run-id".to_string(),
            "/nonexistent/path/that/does/not/exist".to_string(),
        );
        std::env::remove_var("RALPH_GUI_DRY_RUN");
        assert!(result.is_err());
        assert!(
            result.unwrap_err().contains("does not exist"),
            "Expected path-not-found error"
        );
    }
}
