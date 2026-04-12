//! Coordination tool handlers for MCP server.
//!
//! Provides handlers for progress reporting and task completion declaration.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::workspace::Workspace;
use mcp_server::dispatch::registry::ToolError;
use mcp_server::protocol::types::{ToolContent, ToolResult};

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

/// Report agent progress to the Ralph pipeline.
///
/// # Method Identifier
///
/// `report_progress`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::WorkspaceCoordination` (mapped from `RunReportProgress`).
/// Available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe. This is a signaling operation with no filesystem side effects.
///
/// # Request Shape
///
/// ```json
/// {"status": "Implementing auth module", "note": "50% complete"}
/// ```
///
/// ## Required Fields
///
/// - `status` (`string`): Short status message describing current progress.
///
/// ## Optional Fields
///
/// - `note` (`string`, default `""`): Additional detail or context about progress.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Progress reported: status='...', note='...', timestamp=..."}], "isError": false}
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (CapabilityDenied): Session lacks the required capability.
///
/// # Side Effects
///
/// Emits a progress event to the Ralph pipeline observable. No filesystem changes.
///
/// # Idempotency
///
/// Each call emits a distinct timestamped event. Not idempotent.
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

/// Declare that the agent has completed its assigned task.
///
/// # Method Identifier
///
/// `declare_complete`
///
/// # Capability Requirements
///
/// No specific capability required. Available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe. This is a signaling operation with no filesystem side effects.
///
/// # Request Shape
///
/// ```json
/// {"summary": "Implemented the auth module with tests passing"}
/// ```
///
/// ## Optional Fields
///
/// - `summary` (`string`, default `"No summary provided"`): Description of what was accomplished.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Task declared complete: session_id=..., summary='...', timestamp=..."}], "isError": false}
/// ```
///
/// # Error Codes
///
/// None specific.
///
/// # Side Effects
///
/// Emits a completion event to the Ralph pipeline. The session can be closed after
/// this call. The agent should have submitted all required artifacts via
/// `ralph_submit_artifact` before calling this.
///
/// # Idempotency
///
/// Each call emits a distinct timestamped completion event. Calling multiple times
/// is allowed but emits redundant events.
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

/// Format the coordination response text.
fn format_coordination_text(
    action: &str,
    session_id: &str,
    timestamp: u64,
    work_unit_id: Option<&str>,
    payload: Option<&serde_json::Value>,
) -> String {
    let mut message = format!(
        "Coordination action '{}' processed: session_id={}, timestamp={}",
        action, session_id, timestamp
    );
    if let Some(id) = work_unit_id {
        message.push_str(&format!(", work_unit_id={}", id));
    }
    if let Some(p) = payload {
        message.push_str(&format!(", payload={}", p));
    }
    message.push_str("\n[Coordination event emitted to pipeline]");
    message
}

/// Coordinate parallel worker activities.
///
/// # Method Identifier
///
/// `coordinate`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::ArtifactSubmit` — available to parallel worker sessions.
///
/// # Access Mode
///
/// ReadOnly-safe. This is a signaling operation.
///
/// # Request Shape
///
/// ```json
/// {"action": "claim", "work_unit_id": "task-42", "payload": {"step": 1}}
/// ```
///
/// ## Required Fields
///
/// - `action` (`string`): Coordination action. Supported values: `"claim"`, `"release"`,
///   `"status"`, `"ack"`.
///
/// ## Optional Fields
///
/// - `work_unit_id` (`string`): Identifier for the work unit being coordinated.
/// - `payload` (`object`): Additional coordination data (free-form JSON).
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Coordination action 'claim' processed: ..."}], "isError": false}
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (CapabilityDenied): Session lacks `ArtifactSubmit` capability.
/// - JSON-RPC `-32000` (InvalidParams): Missing `action` parameter.
///
/// # Side Effects
///
/// Emits a coordination event to the Ralph pipeline. No filesystem changes.
///
/// # Idempotency
///
/// Each call emits a distinct timestamped event. Not idempotent.
pub fn handle_coordinate(
    session: &AgentSession,
    _workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_capability(
        session,
        Capability::ArtifactSubmit,
        "Workspace coordination",
    )?;
    let action = params
        .get("action")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'action' parameter".to_string()))?;
    let work_unit_id = params.get("work_unit_id").and_then(|v| v.as_str());
    let payload = params.get("payload");

    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let message = format_coordination_text(
        action,
        &session.session_id.to_string(),
        timestamp,
        work_unit_id,
        payload,
    );

    Ok(ToolResult {
        content: vec![ToolContent::text(message)],
        is_error: Some(false),
    })
}

/// Read an environment variable by name.
///
/// # Method Identifier
///
/// `read_env`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::EnvRead` — available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe.
///
/// # Request Shape
///
/// ```json
/// {"name": "RALPH_MCP_ENDPOINT"}
/// ```
///
/// ## Required Fields
///
/// - `name` (`string`): Name of the environment variable to read.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "RALPH_MCP_ENDPOINT=/tmp/ralph-mcp-abc123.sock"}], "isError": false}
/// ```
///
/// Returns `NAME=[not found]` if the variable is not set.
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (CapabilityDenied): Session lacks `EnvRead` capability.
/// - JSON-RPC `-32000` (InvalidParams): Missing `name` parameter.
///
/// # Side Effects
///
/// None. Read-only access to the process environment.
///
/// # Idempotency
///
/// Fully idempotent for the same variable name.
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
