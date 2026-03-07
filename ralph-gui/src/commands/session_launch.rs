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

/// Arguments for launching a new unattended Ralph session.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
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
    if std::env::var("RALPH_GUI_DRY_RUN").as_deref() == Ok("1") {
        let run_id = uuid::Uuid::new_v4().to_string();
        return Ok(run_id);
    }

    let ralph = find_ralph_binary()?;

    let mut cmd = std::process::Command::new(&ralph);
    cmd.current_dir(&target_pb)
        .arg("--prompt")
        .arg(&args.prompt_path)
        .arg("--developer-iters")
        .arg(args.developer_iterations.to_string())
        .arg("--reviewer-passes")
        .arg(args.reviewer_passes.to_string());

    if let Some(ref dev_agent) = args.developer_agent {
        cmd.arg("--developer-agent").arg(dev_agent);
    }
    if let Some(ref rev_agent) = args.reviewer_agent {
        cmd.arg("--reviewer-agent").arg(rev_agent);
    }

    // Spawn detached so the GUI doesn't need to wait for completion.
    let child = cmd
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to spawn ralph: {e}"))?;

    // Store PID for monitoring.
    let pid = child.id();
    let pid_dir = target_pb.join(".agent").join("tmp");
    if let Err(e) = std::fs::create_dir_all(&pid_dir) {
        // Non-fatal: PID file is best-effort.
        eprintln!("Warning: could not create .agent/tmp for PID file: {e}");
    } else {
        let _ = std::fs::write(pid_dir.join("gui-pid"), pid.to_string());
    }

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
pub fn resume_ralph_session(run_id: String, repo_path: String) -> Result<(), String> {
    if run_id.is_empty() {
        return Err("run_id must not be empty".to_string());
    }

    let target_pb = std::path::PathBuf::from(&repo_path);
    if !target_pb.exists() {
        return Err(format!("Repository path does not exist: {repo_path}"));
    }

    // Dry-run mode for tests.
    if std::env::var("RALPH_GUI_DRY_RUN").as_deref() == Ok("1") {
        return Ok(());
    }

    let ralph = find_ralph_binary()?;

    std::process::Command::new(&ralph)
        .current_dir(&target_pb)
        .arg("--resume")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to spawn ralph --resume: {e}"))?;

    Ok(())
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
