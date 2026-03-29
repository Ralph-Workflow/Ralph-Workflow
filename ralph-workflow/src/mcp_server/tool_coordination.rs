//! Coordination tool handlers for MCP server.
//!
//! Provides handlers for progress reporting and task completion declaration.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::mcp_server::tool_registry::ToolError;
use crate::mcp_server::types::{ToolContent, ToolResult};
use crate::workspace::Workspace;

/// Build the progress report response text.
fn format_progress_text(status: &str, note: &str, timestamp: u64) -> String {
    format!(
        "Progress reported: status='{}', note='{}', timestamp={}\n[Progress event emitted to pipeline]",
        status, note, timestamp
    )
}

/// Check a capability and return a CapabilityDenied error if not approved.
fn require_capability(
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

/// Report progress and emit structured notes.
///
/// Requires: `Capability::RunReportProgress`
///
/// Parameters:
/// - `status`: Status message describing current progress
/// - `note`: Optional additional notes or context
pub fn handle_report_progress(
    session: &AgentSession,
    _workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_capability(session, Capability::RunReportProgress, "Progress reporting")?;
    let status = params
        .get("status")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'status' parameter".to_string()))?;
    let note = params.get("note").and_then(|v| v.as_str()).unwrap_or("");
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    Ok(ToolResult {
        content: vec![ToolContent::text(format_progress_text(
            status, note, timestamp,
        ))],
        is_error: Some(false),
    })
}

/// Declare that the agent has completed its task.
///
/// This signals to Ralph that the agent is done and the session can be closed.
/// The agent should have submitted all necessary artifacts before calling this.
///
/// Parameters:
/// - `summary`: Optional summary of what was accomplished
pub fn handle_declare_complete(
    session: &AgentSession,
    _workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    // This tool doesn't require a specific capability - any session can declare complete

    let summary = params
        .get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("No summary provided");

    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    Ok(ToolResult {
        content: vec![ToolContent::text(format!(
            "Task declared complete: session_id={}, summary='{}', timestamp={}\n[Completion event emitted to pipeline]",
            session.session_id, summary, timestamp
        ))],
        is_error: Some(false),
    })
}

/// Read an environment variable.
///
/// Requires: `Capability::EnvRead`
///
/// Parameters:
/// - `name`: Name of the environment variable
pub fn handle_read_env(
    session: &AgentSession,
    _workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_capability(session, Capability::EnvRead, "Environment variable read")?;
    let name = params
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'name' parameter".to_string()))?;
    let value = std::env::var(name).unwrap_or_else(|_| "[not found]".to_string());
    Ok(ToolResult {
        content: vec![ToolContent::text(format!("{}={}", name, value))],
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
    fn test_report_progress_success() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_report_progress(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "status": "Processing files",
                "note": "Completed 5 of 10 files"
            }),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(false));
        assert!(tool_result.content[0].text.contains("Processing files"));
    }

    #[test]
    fn test_report_progress_missing_status() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_report_progress(&session, workspace.as_ref(), serde_json::json!({}));

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }

    #[test]
    fn test_declare_complete() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_declare_complete(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "summary": "Successfully implemented feature X"
            }),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(false));
        assert!(tool_result.content[0].text.contains("declared complete"));
    }

    #[test]
    fn test_read_env() {
        // Set a test environment variable
        std::env::set_var("RALPH_TEST_VAR", "test_value");

        let session = test_session();
        let workspace = test_workspace();

        let result = handle_read_env(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "name": "RALPH_TEST_VAR"
            }),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(tool_result.content[0]
            .text
            .contains("RALPH_TEST_VAR=test_value"));

        // Clean up
        std::env::remove_var("RALPH_TEST_VAR");
    }

    #[test]
    fn test_read_env_not_found() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_read_env(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "name": "NONEXISTENT_VAR_12345"
            }),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(tool_result.content[0].text.contains("[not found]"));
    }

    #[test]
    fn test_read_env_missing_name() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_read_env(&session, workspace.as_ref(), serde_json::json!({}));

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }
}
