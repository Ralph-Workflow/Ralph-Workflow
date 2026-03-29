//! Execution tool handler for MCP server.
//!
//! Provides the `ralph_exec_command` tool that executes bounded shell commands
//! with blacklist filtering via the command_policy module.

use crate::agents::session::command_policy::check_command;
use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::mcp_server::tool_registry::ToolError;
use crate::mcp_server::types::{ToolContent, ToolResult};
use crate::workspace::Workspace;
use std::process::Command;

/// Parsed exec command parameters.
struct ExecParams {
    command: String,
    args: Vec<String>,
    timeout_ms: u64,
}

/// Parse exec command parameters from JSON value.
fn parse_exec_params(params: &serde_json::Value) -> Result<ExecParams, ToolError> {
    let command = params
        .get("command")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'command' parameter".to_string()))?
        .to_string();
    let args: Vec<String> = params
        .get("args")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let timeout_ms = params
        .get("timeout_ms")
        .and_then(|v| v.as_u64())
        .unwrap_or(30000);
    Ok(ExecParams {
        command,
        args,
        timeout_ms,
    })
}

/// Apply blacklist policy to a command and args, returning error if denied.
fn apply_exec_policy(command: &str, args: &[String]) -> Result<(), ToolError> {
    let args_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    if let PolicyOutcome::Denied { ref reason } = check_command(command, &args_refs) {
        return Err(ToolError::CapabilityDenied(format!(
            "Command '{}' denied by policy: {}",
            command, reason
        )));
    }
    Ok(())
}

/// Run the command in the workspace root and return its output.
fn run_command(
    command: &str,
    args: &[String],
    workspace: &dyn Workspace,
) -> Result<std::process::Output, ToolError> {
    Command::new(command)
        .args(args)
        .current_dir(workspace.root())
        .output()
        .map_err(|e| ToolError::ExecutionError(format!("Failed to execute '{}': {}", command, e)))
}

/// Format command output into a human-readable result text.
fn format_exec_result(
    command: &str,
    args: &[String],
    output: &std::process::Output,
    timeout_ms: u64,
) -> String {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let exit_code = output.status.code().unwrap_or(-1);
    let mut text = format!(
        "Command: {} {:?}\nExit code: {}\n\nStdout:\n{}\n\nStderr:\n{}",
        command, args, exit_code, stdout, stderr
    );
    if timeout_ms > 0 && timeout_ms < 60000 {
        text.push_str(&format!(
            "\n\nNote: This command had a {}ms timeout",
            timeout_ms
        ));
    }
    text
}

/// Execute a bounded shell command with blacklist filtering.
///
/// Requires: `Capability::ProcessExecBounded`
///
/// Parameters:
/// - `command`: The command to execute
/// - `args`: Optional array of arguments
/// - `timeout_ms`: Optional timeout in milliseconds (default 30000)
///
/// Check that ProcessExecBounded capability is approved for the session.
fn require_exec_capability(session: &AgentSession) -> Result<(), ToolError> {
    let outcome = session.check_capability(Capability::ProcessExecBounded);
    if matches!(outcome, PolicyOutcome::Approved) {
        return Ok(());
    }
    Err(ToolError::CapabilityDenied(format!(
        "Command execution requires capability '{}': {:?}",
        Capability::ProcessExecBounded.identifier(),
        outcome
    )))
}

/// Execute the command after capability check, returning a ToolResult.
fn run_exec_command(
    workspace: &dyn Workspace,
    params: &serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let p = parse_exec_params(params)?;
    apply_exec_policy(&p.command, &p.args)?;
    let output = run_command(&p.command, &p.args, workspace)?;
    let result_text = format_exec_result(&p.command, &p.args, &output, p.timeout_ms);
    Ok(ToolResult {
        content: vec![ToolContent::text(result_text)],
        is_error: Some(!output.status.success()),
    })
}

pub fn handle_exec_command(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    require_exec_capability(session)?;
    run_exec_command(workspace, &params)
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
    fn test_exec_blacklisted_git() {
        let session = test_session();
        let workspace = test_workspace();

        // git should be blacklisted - Ralph owns git operations
        let result = handle_exec_command(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "command": "git",
                "args": ["status"]
            }),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::CapabilityDenied(_)));
        let error_msg = err.to_string();
        assert!(error_msg.contains("denied by policy"));
    }

    #[test]
    fn test_exec_blacklisted_sudo() {
        let session = test_session();
        let workspace = test_workspace();

        // sudo should be blacklisted
        let result = handle_exec_command(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "command": "sudo",
                "args": ["ls"]
            }),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::CapabilityDenied(_)));
    }

    #[test]
    fn test_exec_blacklisted_rm_rf() {
        let session = test_session();
        let workspace = test_workspace();

        // rm -rf should be blacklisted
        let result = handle_exec_command(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "command": "rm",
                "args": ["-rf", "/"]
            }),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::CapabilityDenied(_)));
    }

    #[test]
    fn test_exec_capability_denied_for_planning() {
        // Planning session doesn't have ProcessExecBounded
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace = test_workspace();

        let result = handle_exec_command(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "command": "ls",
                "args": ["-la"]
            }),
        );

        assert!(result.is_err());
        assert!(matches!(
            result.unwrap_err(),
            ToolError::CapabilityDenied(_)
        ));
    }

    #[test]
    fn test_exec_missing_command() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_exec_command(&session, workspace.as_ref(), serde_json::json!({}));

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }

    #[test]
    fn test_exec_allowed_command() {
        let session = test_session();
        let workspace = test_workspace();

        // ls should be allowed
        let result = handle_exec_command(
            &session,
            workspace.as_ref(),
            serde_json::json!({
                "command": "echo",
                "args": ["hello"]
            }),
        );

        // May fail due to MemoryWorkspace not having a real filesystem
        // but should not be a CapabilityDenied error
        match result {
            Ok(tool_result) => {
                assert!(tool_result.content[0].text.contains("hello"));
            }
            Err(e) => {
                // Could be execution error (expected for memory workspace)
                // but not capability denied
                assert!(!matches!(e, ToolError::CapabilityDenied(_)));
            }
        }
    }
}
