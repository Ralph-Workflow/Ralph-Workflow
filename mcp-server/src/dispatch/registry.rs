//! Tool registry for MCP server.
//!
//! The registry maintains the list of available tools and dispatches
//! tool calls to the appropriate handler.
//!
//! # Tool Lifecycle
//!
//! 1. Registry is created with a set of tool definitions
//! 2. Client calls `tools/list` to get available tools
//! 3. Client calls `tools/call` with tool name and parameters
//! 4. Registry checks capabilities and invokes the handler
//! 5. Handler returns `ToolResult` which is serialized and sent to client
//!
//! # Capability Gating
//!
//! Every tool has a required capability. Before invoking a tool, the registry
//! checks `session.check_capability(required_capability)`. If denied, the tool
//! returns a capability error without invoking the handler.

use crate::dispatch::access::{AccessDecision, McpCapability};
use crate::dispatch::host::{HostSession, WorkspaceAdapter};
use crate::protocol::{ToolContent, ToolDefinition, ToolResult};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use thiserror::Error;

/// Errors that can occur during tool dispatch.
#[derive(Error, Debug)]
pub enum ToolError {
    /// Tool name was not found in the registry.
    #[error("Tool not found: {0}")]
    NotFound(String),
    /// Request parameters are missing or malformed.
    #[error("Invalid parameters: {0}")]
    InvalidParams(String),
    /// Session lacks the capability required for this tool.
    #[error("Capability denied: {0}")]
    CapabilityDenied(String),
    /// Tool handler encountered an error during execution.
    #[error("Execution error: {0}")]
    ExecutionError(String),
}

/// Metadata about a registered tool.
#[derive(Clone)]
pub struct ToolMetadata {
    /// Tool definition advertised to clients.
    pub definition: ToolDefinition,
    /// Capability required to invoke this tool.
    pub required_capability: McpCapability,
    /// Whether this tool performs mutating operations (writes, deletes, exec).
    /// If `None`, derived from `required_capability` at registration time.
    pub is_mutating: Option<bool>,
}

impl ToolMetadata {
    /// Returns whether this tool is mutating, computing from `required_capability`
    /// if not explicitly set.
    pub fn is_mutating(&self) -> bool {
        self.is_mutating
            .unwrap_or_else(|| capability_is_mutating(self.required_capability))
    }
}

/// Returns true if the given capability implies a mutating operation.
pub fn capability_is_mutating(cap: McpCapability) -> bool {
    matches!(
        cap,
        McpCapability::WorkspaceWriteEphemeral
            | McpCapability::WorkspaceWriteTracked
            | McpCapability::WorkspaceWriteAny
            | McpCapability::GitWrite
            | McpCapability::ProcessExecBounded
            | McpCapability::ProcessExecUnbounded
            | McpCapability::EnvWrite
    )
}

/// Tool handler function signature.
///
/// Handlers receive the session and workspace context along with
/// parsed JSON parameters. They return a `ToolResult` on success
/// or a `ToolError` on failure.
pub type ToolHandler = Arc<
    dyn Fn(&dyn HostSession, &dyn WorkspaceAdapter, Value) -> Result<ToolResult, ToolError>
        + Send
        + Sync,
>;

/// Registry of available MCP tools.
///
/// Maintains a map of tool name to metadata + handler.
#[derive(Clone)]
pub struct ToolRegistry {
    tools: HashMap<String, (ToolMetadata, ToolHandler)>,
}

impl ToolRegistry {
    /// Create a new registry with the given tools.
    pub fn new(tools: Vec<(ToolMetadata, ToolHandler)>) -> Self {
        let tools: HashMap<String, (ToolMetadata, ToolHandler)> = tools
            .into_iter()
            .map(|(mut metadata, handler)| {
                // Derive is_mutating from required_capability at registration time if not set.
                // This replaces the hardcoded is_mutating_tool() approach which failed
                // for tools with non-standard prefixes (e.g., "ralph_write_file" vs "write_file").
                if metadata.is_mutating.is_none() {
                    metadata.is_mutating =
                        Some(capability_is_mutating(metadata.required_capability));
                }
                (metadata.definition.name.clone(), (metadata, handler))
            })
            .collect();
        Self { tools }
    }

    /// List all registered tools.
    pub fn list_tools(&self) -> Vec<ToolDefinition> {
        self.tools
            .values()
            .map(|(meta, _)| meta.definition.clone())
            .collect()
    }

    /// Get the required capability for a tool.
    pub fn get_required_capability(&self, tool_name: &str) -> Option<McpCapability> {
        self.tools
            .get(tool_name)
            .map(|(meta, _)| meta.required_capability)
    }

    /// Get metadata for a registered tool.
    pub fn get_metadata(&self, tool_name: &str) -> Option<&ToolMetadata> {
        self.tools.get(tool_name).map(|(meta, _)| meta)
    }

    /// Dispatch a tool call.
    ///
    /// # Arguments
    ///
    /// * `tool_name` - Name of the tool to invoke
    /// * `params` - JSON parameters for the tool
    /// * `session` - Session context for capability checking
    /// * `workspace` - Workspace for file operations
    ///
    /// # Returns
    ///
    /// Returns `ToolResult` on success, `ToolError` on failure.
    pub fn dispatch(
        &self,
        tool_name: &str,
        params: Value,
        session: &dyn HostSession,
        workspace: &dyn WorkspaceAdapter,
    ) -> Result<ToolResult, ToolError> {
        // Look up tool
        let (metadata, handler) = self
            .tools
            .get(tool_name)
            .ok_or_else(|| ToolError::NotFound(tool_name.to_string()))?;

        // Check capability
        let outcome = session.check_capability(metadata.required_capability);
        match outcome {
            AccessDecision::Allow => {}
            AccessDecision::Deny { reason, .. } => {
                return Err(ToolError::CapabilityDenied(reason));
            }
        }

        // Invoke handler
        handler(session, workspace, params)
    }
}

/// Validate required string parameter from JSON params.
pub fn required_string_param(params: &Value, name: &str) -> Result<String, ToolError> {
    params
        .get(name)
        .and_then(|v| v.as_str().map(String::from))
        .ok_or_else(|| ToolError::InvalidParams(format!("Missing '{name}' parameter")))
}

/// Validate optional string parameter from JSON params.
pub fn optional_string_param(params: &Value, name: &str) -> Option<String> {
    params.get(name).and_then(|v| v.as_str().map(String::from))
}

/// Create a text content block.
pub fn text_content(text: impl Into<String>) -> ToolContent {
    ToolContent::text(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatch::host::DirEntry;
    use std::path::Path;

    #[test]
    fn test_registry_not_found() {
        let registry = ToolRegistry::new(vec![]);
        let result = registry.dispatch(
            "nonexistent",
            serde_json::json!({}),
            &MockSession,
            &MockWorkspace,
        );
        assert!(matches!(result, Err(ToolError::NotFound(_))));
    }

    // Mock implementations for testing

    struct MockSession;
    impl HostSession for MockSession {
        fn session_id(&self) -> &str {
            "mock-session"
        }
        fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
            AccessDecision::Allow
        }
    }

    struct MockWorkspace;
    impl WorkspaceAdapter for MockWorkspace {
        fn read(&self, _path: &Path) -> Result<String, String> {
            Ok("mock content".to_string())
        }
        fn write(&self, _path: &Path, _content: &str) -> Result<(), String> {
            Ok(())
        }
        fn exists(&self, _path: &Path) -> bool {
            true
        }
        fn read_dir(&self, _path: &Path) -> Result<Vec<DirEntry>, String> {
            Ok(vec![])
        }
    }
}
