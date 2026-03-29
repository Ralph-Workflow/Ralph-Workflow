//! Tool registry for MCP server RFC-009 Phase 3.
//!
//! This module provides the tool registry that maps MCP tool names to handler
//! functions, with associated capability requirements.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::mcp_server::types::{Tool, ToolResult};
use crate::workspace::Workspace;
use anyhow::Result;
use serde_json::Value;
use std::collections::HashMap;

/// Errors that can occur during tool execution.
#[derive(Debug, thiserror::Error)]
pub enum ToolError {
    #[error("Capability denied: {0}")]
    CapabilityDenied(String),

    #[error("Invalid parameters: {0}")]
    InvalidParams(String),

    #[error("Tool not found: {0}")]
    NotFound(String),

    #[error("Execution error: {0}")]
    ExecutionError(String),

    #[error("Internal error: {0}")]
    InternalError(String),
}

impl ToolError {
    /// Convert to a user-friendly error message for the agent.
    pub fn into_json(self) -> Value {
        serde_json::json!({
            "error": self.to_string()
        })
    }
}

/// A tool handler function that executes a tool call.
type ToolHandler = fn(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: Value,
) -> Result<ToolResult, ToolError>;

/// Metadata about a registered tool.
#[derive(Debug, Clone)]
pub struct ToolMetadata {
    /// The tool definition exposed via MCP.
    pub tool: Tool,
    /// Capabilities required to use this tool.
    pub required_capabilities: Vec<Capability>,
}

/// Tool registry for managing MCP tools and their handlers.
#[derive(Debug, Default)]
pub struct ToolRegistry {
    /// Registered tools by name.
    tools: HashMap<String, ToolMetadata>,
    /// Handler functions by name.
    handlers: HashMap<String, ToolHandler>,
}

/// Check a single capability for a tool, returning an error if denied.
fn check_one_capability(
    session: &AgentSession,
    tool_name: &str,
    cap: Capability,
) -> Result<(), ToolError> {
    let outcome = session.check_capability(cap);
    if matches!(outcome, PolicyOutcome::Approved) {
        return Ok(());
    }
    Err(ToolError::CapabilityDenied(format!(
        "Tool '{}' requires capability '{}' which is not granted: {:?}",
        tool_name,
        cap.identifier(),
        outcome
    )))
}

/// Check that the session has all required capabilities for a tool.
fn check_required_capabilities(
    session: &AgentSession,
    tool_name: &str,
    required_capabilities: &[Capability],
) -> Result<(), ToolError> {
    required_capabilities
        .iter()
        .try_for_each(|&cap| check_one_capability(session, tool_name, cap))
}

impl ToolRegistry {
    /// Create a new empty tool registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a tool with its handler.
    ///
    /// # Arguments
    ///
    /// * `name` - Tool name (e.g., "ralph_read_file")
    /// * `description` - Human-readable description
    /// * `input_schema` - JSON Schema for tool input
    /// * `required_capabilities` - Capabilities needed to use this tool
    /// * `handler` - Handler function
    pub fn register(
        mut self,
        name: impl Into<String>,
        description: impl Into<String>,
        input_schema: Value,
        required_capabilities: Vec<Capability>,
        handler: ToolHandler,
    ) -> Self {
        let name = name.into();
        let tool = Tool::new(name.clone(), description, input_schema);
        let metadata = ToolMetadata {
            tool,
            required_capabilities,
        };

        self.tools.insert(name.clone(), metadata);
        self.handlers.insert(name, handler);
        self
    }

    /// Get the list of all registered tools.
    pub fn list_tools(&self) -> Vec<Tool> {
        self.tools.values().map(|m| m.tool.clone()).collect()
    }

    /// Get tool metadata by name.
    pub fn get_tool(&self, name: &str) -> Option<&ToolMetadata> {
        self.tools.get(name)
    }

    /// Get the handler for a tool by name.
    pub fn get_handler(&self, name: &str) -> Option<ToolHandler> {
        self.handlers.get(name).copied()
    }

    /// Check if a tool exists.
    pub fn has_tool(&self, name: &str) -> bool {
        self.tools.contains_key(name)
    }

    /// Execute a tool call with capability checking.
    ///
    /// Returns an error if:
    /// - The tool doesn't exist
    /// - The session lacks required capabilities
    /// - The handler returns an error
    pub fn execute(
        &self,
        session: &AgentSession,
        workspace: &dyn Workspace,
        tool_name: &str,
        params: Value,
    ) -> Result<ToolResult, ToolError> {
        let metadata = self
            .tools
            .get(tool_name)
            .ok_or_else(|| ToolError::NotFound(tool_name.to_string()))?;
        check_required_capabilities(session, tool_name, &metadata.required_capabilities)?;
        let handler = self
            .handlers
            .get(tool_name)
            .ok_or_else(|| ToolError::NotFound(tool_name.to_string()))?;
        handler(session, workspace, params)
    }

    /// Build a registry with all Ralph MCP tools.
    ///
    /// This creates a registry with all the tools defined in the RFC-009 spec:
    /// - ralph_read_file (WorkspaceRead)
    /// - ralph_list_directory (WorkspaceRead)
    /// - ralph_search_files (WorkspaceRead)
    /// - ralph_write_file (WorkspaceWriteTracked or WorkspaceWriteEphemeral)
    /// - ralph_git_status (GitStatusRead)
    /// - ralph_git_diff (GitDiffRead)
    /// - ralph_git_log (GitStatusRead)
    /// - ralph_git_show (GitStatusRead)
    /// - ralph_exec_command (ProcessExecBounded)
    /// - ralph_submit_artifact (ArtifactSubmit)
    /// - ralph_report_progress (RunReportProgress)
    /// - ralph_read_env (EnvRead)
    /// - ralph_git_commit (GitWrite)
    pub fn with_ralph_tools() -> Self {
        use crate::agents::session::Capability;
        use crate::mcp_server::tool_artifact::handle_submit_artifact;
        use crate::mcp_server::tool_coordination::{
            handle_declare_complete, handle_read_env, handle_report_progress,
        };
        use crate::mcp_server::tool_exec::handle_exec_command;
        use crate::mcp_server::tool_git_read::{
            handle_git_diff, handle_git_log, handle_git_show, handle_git_status,
        };
        use crate::mcp_server::tool_workspace::{
            handle_list_directory, handle_read_file, handle_search_files, handle_write_file,
        };

        let mut registry = Self::new();

        // Workspace read tools
        let read_file_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read"
                }
            },
            "required": ["path"]
        });
        registry = registry.register(
            "ralph_read_file",
            "Read file contents by path",
            read_file_schema,
            vec![Capability::WorkspaceRead],
            |session, workspace, params| handle_read_file(session, workspace, params),
        );

        let list_dir_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively"
                }
            },
            "required": ["path"]
        });
        registry = registry.register(
            "ralph_list_directory",
            "List directory contents",
            list_dir_schema,
            vec![Capability::WorkspaceRead],
            |session, workspace, params| handle_list_directory(session, workspace, params),
        );

        let search_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern"
                },
                "path": {
                    "type": "string",
                    "description": "Path to search in"
                }
            },
            "required": ["pattern", "path"]
        });
        registry = registry.register(
            "ralph_search_files",
            "Search file contents",
            search_schema,
            vec![Capability::WorkspaceRead],
            |session, workspace, params| handle_search_files(session, workspace, params),
        );

        // Workspace write tool
        let write_file_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write"
                }
            },
            "required": ["path", "content"]
        });
        // OR-capability: a session can write if it has WorkspaceWriteTracked OR
        // WorkspaceWriteEphemeral. The registry uses AND logic for required_capabilities,
        // so the OR check lives entirely in handle_write_file — no required_capabilities here.
        registry = registry.register(
            "ralph_write_file",
            "Write content to a file",
            write_file_schema,
            vec![],
            |session, workspace, params| handle_write_file(session, workspace, params),
        );

        // Git read tools
        let git_status_schema = serde_json::json!({
            "type": "object",
            "properties": {}
        });
        registry = registry.register(
            "ralph_git_status",
            "Read git status",
            git_status_schema,
            vec![Capability::GitStatusRead],
            |session, workspace, params| handle_git_status(session, workspace, params),
        );

        let git_diff_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments to pass to git diff"
                }
            }
        });
        registry = registry.register(
            "ralph_git_diff",
            "Read git diff",
            git_diff_schema,
            vec![Capability::GitDiffRead],
            |session, workspace, params| handle_git_diff(session, workspace, params),
        );

        let git_log_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of commits to show"
                }
            }
        });
        registry = registry.register(
            "ralph_git_log",
            "Read git log",
            git_log_schema,
            vec![Capability::GitStatusRead],
            |session, workspace, params| handle_git_log(session, workspace, params),
        );

        let git_show_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Git object reference (commit, tag, etc.)"
                }
            },
            "required": ["ref"]
        });
        registry = registry.register(
            "ralph_git_show",
            "Show git object",
            git_show_schema,
            vec![Capability::GitStatusRead],
            |session, workspace, params| handle_git_show(session, workspace, params),
        );

        // Execution tool
        let exec_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command to execute"
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command arguments"
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout in milliseconds"
                }
            },
            "required": ["command"]
        });
        registry = registry.register(
            "ralph_exec_command",
            "Execute a bounded shell command with blacklist filtering",
            exec_schema,
            vec![Capability::ProcessExecBounded],
            |session, workspace, params| handle_exec_command(session, workspace, params),
        );

        // Artifact submission
        let artifact_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "artifact_type": {
                    "type": "string",
                    "description": "Type of artifact: plan, development_result, fix_result, issues, commit_message",
                    "enum": ["plan", "development_result", "fix_result", "issues", "commit_message"]
                },
                "content": {
                    "type": "string",
                    "description": "JSON-serialized artifact content validated against the artifact type's schema"
                },
                "partial": {
                    "type": "boolean",
                    "description": "When true, allows missing optional fields. Partial artifacts are persisted for agent resumption.",
                    "default": false
                }
            },
            "required": ["artifact_type", "content"]
        });
        registry = registry.register(
            "ralph_submit_artifact",
            "Submit a structured artifact",
            artifact_schema,
            vec![Capability::ArtifactSubmit],
            |session, workspace, params| handle_submit_artifact(session, workspace, params),
        );

        // Progress reporting
        let progress_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Status message"
                },
                "note": {
                    "type": "string",
                    "description": "Additional note"
                }
            },
            "required": ["status"]
        });
        registry = registry.register(
            "ralph_report_progress",
            "Report progress and emit structured notes",
            progress_schema,
            vec![Capability::RunReportProgress],
            |session, workspace, params| handle_report_progress(session, workspace, params),
        );

        // Declare complete
        let declare_complete_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Optional summary of what was accomplished"
                }
            }
        });
        registry = registry.register(
            "ralph_declare_complete",
            "Declare that the agent has completed its task",
            declare_complete_schema,
            vec![], // No specific capability required - any session can declare complete
            |session, workspace, params| handle_declare_complete(session, workspace, params),
        );

        // Environment read
        let env_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Environment variable name"
                }
            },
            "required": ["name"]
        });
        registry = registry.register(
            "ralph_read_env",
            "Read an environment variable",
            env_schema,
            vec![Capability::EnvRead],
            |session, workspace, params| handle_read_env(session, workspace, params),
        );

        // Git write (commit)
        let git_commit_schema = serde_json::json!({
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message"
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to commit"
                }
            },
            "required": ["message"]
        });
        registry = registry.register(
            "ralph_git_commit",
            "Perform a git commit",
            git_commit_schema,
            vec![Capability::GitWrite],
            |_session, _workspace, params| {
                let _message = params
                    .get("message")
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| {
                        ToolError::InvalidParams("Missing 'message' parameter".to_string())
                    })?;
                let _files: Vec<String> = params
                    .get("files")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default();

                // Return NotFound to indicate this tool is not available in this session type.
                // This is a protocol-level response, not a tool-level isError response.
                Err(ToolError::NotFound(
                    "ralph_git_commit not implemented in MCP session. \
                     Use CLI plumbing (--generate-commit/--apply-commit) instead."
                        .to_string(),
                ))
            },
        );

        registry
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(feature = "test-utils")]
    use crate::agents::session::{AgentSession, SessionDrain};
    #[cfg(feature = "test-utils")]
    use std::sync::Arc;

    #[test]
    fn test_registry_has_ralph_tools() {
        let registry = ToolRegistry::with_ralph_tools();

        // Should have all defined tools
        let tools = registry.list_tools();
        let tool_names: Vec<_> = tools.iter().map(|t| t.name.clone()).collect();

        assert!(tool_names.contains(&"ralph_read_file".to_string()));
        assert!(tool_names.contains(&"ralph_write_file".to_string()));
        assert!(tool_names.contains(&"ralph_git_status".to_string()));
        assert!(tool_names.contains(&"ralph_git_diff".to_string()));
        assert!(tool_names.contains(&"ralph_exec_command".to_string()));
        assert!(tool_names.contains(&"ralph_submit_artifact".to_string()));
    }

    #[cfg(feature = "test-utils")]
    fn test_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
    }

    #[test]
    #[cfg(feature = "test-utils")]
    fn test_tool_not_found() {
        use crate::workspace::memory_workspace::MemoryWorkspace;

        let registry = ToolRegistry::with_ralph_tools();
        let session = test_session();
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());

        let result = registry.execute(
            &session,
            workspace.as_ref(),
            "nonexistent_tool",
            serde_json::json!({}),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::NotFound(_)));
    }

    #[test]
    #[cfg(feature = "test-utils")]
    fn test_capability_denied() {
        use crate::workspace::memory_workspace::MemoryWorkspace;
        use std::path::Path;

        let registry = ToolRegistry::with_ralph_tools();
        // Planning session has WorkspaceWriteEphemeral but NOT WorkspaceWriteTracked.
        // Writing an existing tracked file must be denied.
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());

        // Pre-create the file so it is treated as tracked (exists + not under .agent/).
        workspace
            .write(Path::new("tracked.rs"), "fn main() {}")
            .expect("pre-seed tracked file");

        let result = registry.execute(
            &session,
            workspace.as_ref(),
            "ralph_write_file",
            serde_json::json!({"path": "tracked.rs", "content": "changed"}),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::CapabilityDenied(_)));
    }

    #[test]
    #[cfg(feature = "test-utils")]
    fn test_git_commit_tool_fails_closed_until_implemented() {
        use crate::workspace::memory_workspace::MemoryWorkspace;

        let registry = ToolRegistry::with_ralph_tools();
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 1);
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());

        let result = registry.execute(
            &session,
            workspace.as_ref(),
            "ralph_git_commit",
            serde_json::json!({"message": "feat: test"}),
        );

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, ToolError::NotFound(_)));
        assert!(
            err.to_string().contains("not implemented"),
            "error should explicitly state tool is not implemented"
        );
    }

    #[test]
    #[cfg(feature = "test-utils")]
    fn test_read_file_with_dev_session() {
        use crate::workspace::memory_workspace::MemoryWorkspace;

        let registry = ToolRegistry::with_ralph_tools();
        let session = test_session();
        let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());

        // Create a test file in the workspace
        workspace
            .write(std::path::Path::new("test.txt"), "hello world")
            .expect("create test file");

        let result = registry.execute(
            &session,
            workspace.as_ref(),
            "ralph_read_file",
            serde_json::json!({"path": "test.txt"}),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(false));
        // Verify content
        let content = &tool_result.content[0].text;
        assert!(content.contains("hello world"));
    }
}
