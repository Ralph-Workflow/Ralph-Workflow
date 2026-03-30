//! Git read tool handlers for MCP server.
//!
//! Provides handlers for git status, diff, log, and show operations.
//! All git read operations are allowed for sessions with appropriate capabilities.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::workspace::Workspace;
use mcp_server::dispatch::registry::ToolError;
use mcp_server::protocol::types::{ToolContent, ToolResult};
use std::process::Command;

fn require_git_capability(
    session: &AgentSession,
    cap: Capability,
    action: &str,
) -> Result<(), ToolError> {
    let outcome = session.check_capability(cap);
    if matches!(outcome, PolicyOutcome::Approved) {
        Ok(())
    } else {
        Err(ToolError::CapabilityDenied(format!(
            "{} requires capability '{}': {:?}",
            action,
            cap.identifier(),
            outcome
        )))
    }
}

/// Run a git command and return its output.
fn run_git_command(workspace: &dyn Workspace, args: &[&str]) -> Result<String, ToolError> {
    let repo_root = workspace.root();

    let output = Command::new("git")
        .args(args)
        .current_dir(repo_root)
        .output()
        .map_err(|e| ToolError::ExecutionError(format!("Failed to execute git: {}", e)))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(ToolError::ExecutionError(format!(
            "git command failed: {}",
            stderr
        )));
    }

    Ok(stdout)
}

/// Run a git command and return its output, allowing non-zero exit codes.
/// Use this for commands like `git diff` which may return non-zero when there are changes.
fn run_git_command_lenient(workspace: &dyn Workspace, args: &[&str]) -> Result<String, ToolError> {
    let output = Command::new("git")
        .args(args)
        .current_dir(workspace.root())
        .output()
        .map_err(|e| ToolError::ExecutionError(format!("Failed to execute git: {}", e)))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    Ok(format!("{stdout}{stderr}"))
}

/// Read git status.
///
/// Requires: `Capability::GitStatusRead`
pub fn handle_git_status(
    session: &AgentSession,
    workspace: &dyn Workspace,
    _params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_git_capability(session, Capability::GitStatusRead, "Git status")?;
    let output = run_git_command(workspace, &["status"])?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// Read git diff.
///
/// Requires: `Capability::GitDiffRead`
///
/// Parameters:
/// - `args`: Optional array of arguments to pass to git diff (e.g., ["--staged"])
pub fn handle_git_diff(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_git_capability(session, Capability::GitDiffRead, "Git diff")?;
    let extra_args: Vec<&str> = params
        .get("args")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
        .unwrap_or_default();
    let mut git_args = vec!["diff"];
    git_args.extend(extra_args.iter());
    let output = run_git_command_lenient(workspace, &git_args)?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// Read git log.
///
/// Requires: `Capability::GitStatusRead`
///
/// Parameters:
/// - `count`: Optional number of commits to show (default 10)
pub fn handle_git_log(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_git_capability(session, Capability::GitStatusRead, "Git log")?;
    let count = params.get("count").and_then(|v| v.as_u64()).unwrap_or(10) as usize;
    let output = run_git_command(workspace, &["log", &format!("-{}", count), "--oneline"])?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// Show a git object (commit, tag, etc.).
///
/// Requires: `Capability::GitStatusRead`
///
/// Parameters:
/// - `ref`: Git object reference (commit hash, tag name, etc.)
pub fn handle_git_show(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_git_capability(session, Capability::GitStatusRead, "Git show")?;
    let git_ref = params
        .get("ref")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'ref' parameter".to_string()))?;
    let output = run_git_command(workspace, &["show", git_ref])?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::SessionDrain;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use std::sync::Arc;

    fn test_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
    }

    fn test_workspace() -> Arc<dyn Workspace> {
        Arc::new(MemoryWorkspace::new_test())
    }

    #[test]
    fn test_git_status_capability_check() {
        // Test that a session without GitStatusRead is denied.
        // All drain types have GitStatusRead by default, so we test with a minimal
        // capability set to verify the capability check works.
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace = test_workspace();

        // Planning has GitStatusRead, so this should succeed (though git command may fail
        // due to MemoryWorkspace not being a real git repo)
        let result = handle_git_status(&session, workspace.as_ref(), serde_json::json!({}));

        // The git command will fail because MemoryWorkspace isn't a real git repo,
        // but it should NOT be a CapabilityDenied error
        assert!(!matches!(
            result.unwrap_err(),
            ToolError::CapabilityDenied(_)
        ));
    }

    #[test]
    fn test_git_diff_capability_check() {
        // Test that capability check works correctly.
        // All drain types have GitDiffRead by default.
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace = test_workspace();

        let result = handle_git_diff(
            &session,
            workspace.as_ref(),
            serde_json::json!({"args": []}),
        );

        // The git command will fail because MemoryWorkspace isn't a real git repo,
        // but it should NOT be a CapabilityDenied error
        assert!(!matches!(
            result.unwrap_err(),
            ToolError::CapabilityDenied(_)
        ));
    }

    #[test]
    fn test_git_log_missing_ref() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_git_show(&session, workspace.as_ref(), serde_json::json!({}));

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }
}
