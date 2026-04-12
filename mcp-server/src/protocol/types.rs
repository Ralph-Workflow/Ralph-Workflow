//! MCP protocol types for JSON-RPC communication.
//!
//! This module contains pure data types with no side effects. All types are
//! serializable/deserializable with serde for JSON-RPC encoding.
//!
//! # RPC Contract
//!
//! The MCP protocol uses JSON-RPC 2.0 with Content-Length framing. Every request
//! must include a Content-Length header indicating the byte length of the body.
//!
//! ## Method Identifiers
//!
//! | Method | Description |
//! |--------|-------------|
//! | `initialize` | Initial handshake - client sends capabilities, server responds |
//! | `ping` | Liveness check - no params, returns null |
//! | `tools/list` | List available tools |
//! | `tools/call` | Invoke a tool by name with parameters |
//! | `resources/list` | List available resources (placeholder) |
//! | `prompts/list` | List available prompts (placeholder) |
//! | `completion/complete` | Request completion suggestions (placeholder) |
//!
//! ## Error Codes
//!
//! | Code | Meaning |
//! |------|---------|
//! | -32700 | Parse error - invalid JSON |
//! | -32600 | Invalid request - missing required fields |
//! | -32601 | Method not found - unknown method |
//! | -32602 | Invalid params - wrong parameter types |
//! | -32603 | Internal error - server-side failure |
//! | -32000 | Tool error - tool execution failed |

use serde::{Deserialize, Serialize};

/// MCP protocol version supported by this server.
pub const MCP_PROTOCOL_VERSION: &str = "2024-11-05";

// ---------------------------------------------------------------------------
// JSON-RPC 2.0 Base Types
// ---------------------------------------------------------------------------

/// JSON-RPC 2.0 request envelope.
///
/// # Request Shape
/// ```json
/// {
///   "jsonrpc": "2.0",
///   "method": "tools/list",
///   "params": { ... },
///   "id": 1
/// }
/// ```
///
/// Required fields: `jsonrpc`, `method`
/// Optional fields: `params` (required for some methods), `id` (notifications have no id)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    /// JSON-RPC version - must be exactly "2.0"
    pub jsonrpc: String,
    /// Method name to invoke
    pub method: String,
    /// Method parameters (method-specific structure)
    #[serde(default)]
    pub params: Option<serde_json::Value>,
    /// Request identifier for response correlation.
    /// None indicates a notification (no response should be sent).
    #[serde(default)]
    pub id: Option<serde_json::Value>,
}

/// JSON-RPC 2.0 response envelope.
///
/// # Success Response Shape
/// ```json
/// {
///   "jsonrpc": "2.0",
///   "result": { ... },
///   "id": 1
/// }
/// ```
///
/// # Error Response Shape
/// ```json
/// {
///   "jsonrpc": "2.0",
///   "error": { "code": -32602, "message": "...", "data": ... },
///   "id": 1
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcResponse {
    /// JSON-RPC version - must be exactly "2.0"
    pub jsonrpc: String,
    /// Result on success, omitted on error
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    /// Error on failure, omitted on success
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
    /// Request identifier for correlation
    pub id: serde_json::Value,
}

impl JsonRpcResponse {
    /// Create a success response.
    pub fn success(result: serde_json::Value, id: serde_json::Value) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            result: Some(result),
            error: None,
            id,
        }
    }

    /// Create an error response.
    pub fn error(error: JsonRpcError, id: serde_json::Value) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            result: None,
            error: Some(error),
            id,
        }
    }
}

/// JSON-RPC 2.0 error object.
///
/// # Error Shape
/// ```json
/// {
///   "code": -32602,
///   "message": "Invalid params",
///   "data": { "param": "name", "reason": "required" }
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcError {
    /// Numeric error code (see error code table in module docs)
    pub code: i32,
    /// Human-readable error message
    pub message: String,
    /// Additional error details (optional)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

impl JsonRpcError {
    /// Parse error (-32700): Invalid JSON received.
    pub fn parse_error() -> Self {
        Self {
            code: -32700,
            message: "Parse error - invalid JSON".to_string(),
            data: None,
        }
    }

    /// Invalid request (-32600): The JSON sent is not a valid request object.
    pub fn invalid_request() -> Self {
        Self {
            code: -32600,
            message: "Invalid request".to_string(),
            data: None,
        }
    }

    /// Method not found (-32601): The method does not exist or is not available.
    pub fn method_not_found() -> Self {
        Self {
            code: -32601,
            message: "Method not found".to_string(),
            data: None,
        }
    }

    /// Invalid params (-32602): Invalid method parameters.
    pub fn invalid_params(msg: impl Into<String>) -> Self {
        Self {
            code: -32602,
            message: msg.into(),
            data: None,
        }
    }

    /// Internal error (-32603): Internal JSON-RPC server error.
    pub fn internal_error() -> Self {
        Self {
            code: -32603,
            message: "Internal error".to_string(),
            data: None,
        }
    }

    /// Internal error (-32603) with structured data: Internal JSON-RPC server error
    /// with additional error details in the data field.
    pub fn internal_error_with_data(msg: impl Into<String>, data: serde_json::Value) -> Self {
        Self {
            code: -32603,
            message: msg.into(),
            data: Some(data),
        }
    }

    /// Tool error (-32000): Tool execution failed.
    pub fn tool_error(msg: impl Into<String>) -> Self {
        Self {
            code: -32000,
            message: msg.into(),
            data: None,
        }
    }

    /// Tool error (-32000) with structured data: Tool execution failed
    /// with additional error details in the data field.
    pub fn tool_error_with_data(msg: impl Into<String>, data: serde_json::Value) -> Self {
        Self {
            code: -32000,
            message: msg.into(),
            data: Some(data),
        }
    }

    /// Server not initialized: Server not ready to accept requests.
    pub fn not_initialized() -> Self {
        Self {
            code: -32001,
            message: "Server not initialized - call initialize first".to_string(),
            data: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Initialize Handshake
// ---------------------------------------------------------------------------

/// Parameters for the `initialize` method.
///
/// # Request Shape
/// ```json
/// {
///   "protocolVersion": "2024-11-05",
///   "capabilities": { ... },
///   "clientInfo": { "name": "Claude Code", "version": "1.0" }
/// }
/// ```
///
/// Required fields: `protocolVersion`
/// Optional fields: `capabilities`, `clientInfo`
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InitializeParams {
    /// Client-supported protocol version.
    /// Server will use this or the oldest supported version.
    #[serde(rename = "protocolVersion")]
    pub protocol_version: String,
    /// Client capabilities (currently unused by Ralph).
    #[serde(default)]
    pub capabilities: ClientCapabilities,
    /// Information about the client application.
    #[serde(rename = "clientInfo", skip_serializing_if = "Option::is_none")]
    pub client_info: Option<ClientInfo>,
}

/// Client capabilities declaration (placeholder for future use).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ClientCapabilities {
    /// Sampling capability (placeholder).
    #[serde(default)]
    pub sampling: serde_json::Value,
}

/// Information about the client application.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientInfo {
    /// Client application name.
    pub name: String,
    /// Client application version.
    pub version: String,
}

/// Result of the `initialize` handshake.
///
/// # Response Shape
/// ```json
/// {
///   "protocolVersion": "2024-11-05",
///   "capabilities": { "tools": { "listChanged": true } },
///   "serverInfo": { "name": "ralph-mcp", "version": "0.7.13" }
/// }
/// ```
///
/// The server returns its protocol version, capabilities, and server info.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InitializeResult {
    /// Server-supported protocol version.
    #[serde(rename = "protocolVersion")]
    pub protocol_version: String,
    /// Server capabilities - declares what the server supports.
    pub capabilities: ServerCapabilities,
    /// Information about the server application.
    #[serde(rename = "serverInfo")]
    pub server_info: ServerInfo,
}

/// Information about the server application.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerInfo {
    /// Server application name.
    pub name: String,
    /// Server application version.
    pub version: String,
}

// ---------------------------------------------------------------------------
// Server Capabilities
// ---------------------------------------------------------------------------

/// Server capabilities declaration.
///
/// # Capabilities Shape
/// ```json
/// {
///   "tools": { "listChanged": true }
/// }
/// ```
///
/// Currently Ralph only advertises tools capability.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ServerCapabilities {
    /// Tools capability declaration.
    #[serde(default)]
    pub tools: Option<ToolsCapability>,
}

/// Tools capability details.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolsCapability {
    /// Whether the tools list may change during the session.
    /// Always true for Ralph since tool availability depends on session capabilities.
    #[serde(rename = "listChanged", default)]
    pub list_changed: bool,
}

// ---------------------------------------------------------------------------
// Tool Types
// ---------------------------------------------------------------------------

/// Tool definition as advertised in `tools/list`.
///
/// # Tool Shape
/// ```json
/// {
///   "name": "ralph_workspace_read_file",
///   "description": "Read a file from the workspace",
///   "inputSchema": {
///     "type": "object",
///     "properties": { "path": { "type": "string" } },
///     "required": ["path"]
///   }
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    /// Unique tool name - used to invoke the tool.
    pub name: String,
    /// Human-readable description of what the tool does.
    pub description: String,
    /// JSON Schema for the tool's input parameters.
    #[serde(rename = "inputSchema")]
    pub input_schema: serde_json::Value,
}

/// Result of a tool invocation.
///
/// # Result Shape
/// ```json
/// {
///   "content": [
///     { "type": "text", "text": "file contents here" }
///   ],
///   "isError": false
/// }
/// ```
///
/// `isError` should be true when tool execution failed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    /// List of content blocks returned by the tool.
    pub content: Vec<ToolContent>,
    /// Whether the tool encountered an error.
    /// When true, content describes the error.
    #[serde(rename = "isError", skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
}

impl ToolResult {
    /// Create a successful tool result.
    pub fn success(content: Vec<ToolContent>) -> Self {
        Self {
            content,
            is_error: Some(false),
        }
    }

    /// Create an error tool result.
    pub fn error(text: impl Into<String>) -> Self {
        Self {
            content: vec![ToolContent::text(text)],
            is_error: Some(true),
        }
    }
}

/// Content block in a tool result.
///
/// # Content Block Shape
/// ```json
/// {
///   "type": "text",
///   "text": "content here"
/// }
/// ```
///
/// Currently only "text" type is supported.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolContent {
    /// Content type - currently only "text" is supported.
    #[serde(rename = "type")]
    pub content_type: String,
    /// The actual content text.
    pub text: String,
}

impl ToolContent {
    /// Create a text content block.
    pub fn text(text: impl Into<String>) -> Self {
        Self {
            content_type: "text".to_string(),
            text: text.into(),
        }
    }
}

// ---------------------------------------------------------------------------
// Artifact Validation Errors (RFC-009 Phase 3)
// ---------------------------------------------------------------------------

/// Error codes for artifact validation failures.
///
/// These use SCREAMING_SNAKE_CASE serialization for JSON compatibility.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    /// Required field is missing from the artifact.
    MissingField,
    /// Field value is not one of the allowed enum values.
    InvalidEnum,
    /// Field value does not match the expected type.
    TypeMismatch,
    /// Field value violates a constraint (e.g., too long, wrong format).
    ConstraintViolation,
}

/// Validation error for a single field in an artifact.
///
/// # Validation Error Shape
/// ```json
/// {
///   "code": "MISSING_FIELD",
///   "field_path": "artifacts[0].description",
///   "expected": "non-empty string",
///   "got": null,
///   "next_actions": ["add description field"],
///   "prohibition": null
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationError {
    /// Error code categorizing the failure.
    pub code: ErrorCode,
    /// JSON path to the failing field.
    #[serde(rename = "fieldPath")]
    pub field_path: String,
    /// Human-readable description of expected value.
    pub expected: String,
    /// Actual value that was received.
    pub got: serde_json::Value,
    /// Suggested actions to fix the error.
    #[serde(rename = "nextActions", default)]
    pub next_actions: Vec<String>,
    /// If present, indicates a forbidden action.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prohibition: Option<String>,
}

/// Response when artifact validation fails.
///
/// # Error Response Shape
/// ```json
/// {
///   "errors": [ ... ],
///   "artifact_type": "development_result"
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorResponse {
    /// List of validation errors.
    pub errors: Vec<ValidationError>,
    /// Type of artifact that failed validation.
    #[serde(rename = "artifactType")]
    pub artifact_type: String,
}

// ---------------------------------------------------------------------------
// Null Result
// ---------------------------------------------------------------------------

/// Null result type for notifications and responses that return no data.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct NullResult {}
