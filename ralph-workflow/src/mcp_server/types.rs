//! MCP protocol types for RFC-009 Phase 3.
//!
//! This module defines the core types for the Model Context Protocol (MCP)
//! JSON-RPC communication between Ralph and agents.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// MCP protocol version supported by Ralph.
pub const MCP_PROTOCOL_VERSION: &str = "2024-11-05";

/// JSON-RPC request envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    /// JSON-RPC protocol version (must be "2.0").
    pub jsonrpc: String,
    /// Request method name.
    pub method: String,
    /// Request parameters (optional).
    #[serde(default)]
    pub params: Option<Value>,
    /// Request identifier for correlation.
    #[serde(default)]
    pub id: Option<Value>,
}

/// JSON-RPC response envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcResponse {
    /// JSON-RPC protocol version (must be "2.0").
    pub jsonrpc: String,
    /// Response result (present if successful).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    /// Response error (present if failed).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
    /// Request identifier being responded to.
    pub id: Value,
}

/// JSON-RPC error object.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcError {
    /// Error code.
    pub code: i32,
    /// Error message.
    pub message: String,
    /// Additional error data (optional).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl JsonRpcError {
    /// Create a method not found error.
    pub fn method_not_found(method: &str) -> Self {
        Self {
            code: -32601,
            message: format!("Method not found: {}", method),
            data: None,
        }
    }

    /// Create an invalid params error.
    pub fn invalid_params(message: &str) -> Self {
        Self {
            code: -32602,
            message: message.to_string(),
            data: None,
        }
    }

    /// Create an internal error.
    pub fn internal_error(message: &str) -> Self {
        Self {
            code: -32603,
            message: message.to_string(),
            data: None,
        }
    }

    /// Create an error for tool execution failure.
    pub fn tool_error(message: &str) -> Self {
        Self {
            code: -32000,
            message: message.to_string(),
            data: None,
        }
    }
}

/// MCP server capabilities advertised during initialization.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ServerCapabilities {
    /// Whether the server supports tool calls.
    #[serde(default)]
    pub tools: Option<ToolsCapability>,
}

impl ServerCapabilities {
    /// Create capabilities with tools enabled.
    pub fn with_tools() -> Self {
        Self {
            tools: Some(ToolsCapability {
                list_changed: Some(true),
            }),
        }
    }
}

/// Tools capability.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolsCapability {
    /// Whether the tool list can change during the session.
    #[serde(default)]
    pub list_changed: Option<bool>,
}

/// Tool definition for tools/list response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tool {
    /// Unique tool name.
    pub name: String,
    /// Human-readable description.
    pub description: String,
    /// JSON Schema for tool input.
    pub input_schema: Value,
}

impl Tool {
    /// Create a new tool with the given name, description, and input schema.
    pub fn new(
        name: impl Into<String>,
        description: impl Into<String>,
        input_schema: Value,
    ) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            input_schema,
        }
    }
}

/// Result of a tool call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    /// Tool call output (content array).
    pub content: Vec<ToolContent>,
    /// Whether the tool call resulted in an error. Serialized as "isError" per MCP spec.
    #[serde(rename = "isError", skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
}

/// Content block in a tool result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolContent {
    /// Content type (currently only "text" is supported).
    #[serde(rename = "type")]
    pub content_type: String,
    /// Content value.
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

/// Initialize request parameters from client.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct InitializeParams {
    /// Client protocol version.
    pub protocol_version: Option<String>,
    /// Client capabilities.
    #[serde(default)]
    pub capabilities: ClientCapabilities,
    /// Client name (optional).
    pub client_info: Option<ClientInfo>,
}

/// Client capabilities sent during initialization.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ClientCapabilities {
    /// Sampling capability (not used by Ralph).
    #[serde(default)]
    pub sampling: Option<Value>,
}

/// Client information.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientInfo {
    /// Client name.
    pub name: String,
    /// Client version.
    pub version: String,
}

/// Initialize request result.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeResult {
    /// Server protocol version.
    pub protocol_version: String,
    /// Server capabilities.
    pub capabilities: ServerCapabilities,
    /// Server info.
    pub server_info: ServerInfo,
}

/// Server information.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerInfo {
    /// Server name.
    pub name: String,
    /// Server version.
    pub version: String,
}

impl InitializeResult {
    /// Create the server's response to initialization.
    pub fn new() -> Self {
        Self {
            protocol_version: MCP_PROTOCOL_VERSION.to_string(),
            capabilities: ServerCapabilities::with_tools(),
            server_info: ServerInfo {
                name: "ralph".to_string(),
                version: env!("CARGO_PKG_VERSION").to_string(),
            },
        }
    }
}

impl Default for InitializeResult {
    fn default() -> Self {
        Self::new()
    }
}

// =============================================================================
// ARTIFACT VALIDATION ERROR TYPES
// =============================================================================

/// Error codes for artifact validation failures.
///
/// Exactly 4 variants covering all JSON Schema validation failure categories.
/// Serializes to SCREAMING_SNAKE_CASE for machine parseability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    /// Required field is absent or null.
    MissingField,
    /// Value not in allowed enum set.
    InvalidEnum,
    /// Value type does not match schema (e.g., string where integer expected).
    TypeMismatch,
    /// Value violates a schema constraint (minItems, pattern, etc.).
    ConstraintViolation,
}

/// A single field-level validation error with directive-style recovery actions.
///
/// Each error pinpoints a specific field and tells the agent exactly what to do.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidationError {
    /// Which category of validation failure occurred.
    pub code: ErrorCode,
    /// JSON-path-subset pointing to the offending field (e.g., "steps[0].title").
    pub field_path: String,
    /// What the schema expected (e.g., "non-empty string", "integer >= 1").
    pub expected: String,
    /// What was actually found, serialized as a string. None if the field was absent.
    pub got: Option<String>,
    /// 1-3 imperative sentences telling the agent what to do.
    pub next_actions: Vec<String>,
    /// Optional imperative sentence telling the agent what NOT to do.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prohibition: Option<String>,
}

/// Complete validation error response for an artifact submission.
///
/// Carried in `JsonRpcError.data` when validation fails.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ErrorResponse {
    /// One or more validation errors found in the artifact.
    pub errors: Vec<ValidationError>,
    /// The artifact type that was being validated (e.g., "plan").
    pub artifact_type: String,
}

impl ErrorResponse {
    /// Create a new error response for the given artifact type.
    ///
    /// # Panics
    ///
    /// Panics in debug builds if `errors` is empty. In release builds,
    /// returns the response as-is (caller is responsible for non-empty errors).
    pub fn new(artifact_type: impl Into<String>, errors: Vec<ValidationError>) -> Self {
        debug_assert!(
            !errors.is_empty(),
            "ErrorResponse must contain at least one error"
        );
        Self {
            errors,
            artifact_type: artifact_type.into(),
        }
    }

    /// Convert this error response into a `JsonRpcError` for the JSON-RPC envelope.
    pub fn into_json_rpc_error(self) -> JsonRpcError {
        let count = self.errors.len();
        let message = format!(
            "Artifact validation failed: {} error{}",
            count,
            if count == 1 { "" } else { "s" }
        );
        JsonRpcError {
            code: -32602,
            message,
            data: serde_json::to_value(self).ok(),
        }
    }
}

impl ValidationError {
    /// Create a missing field error.
    pub fn missing_field(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::MissingField,
            field_path: field_path.into(),
            expected: expected.into(),
            got: None,
            next_actions,
            prohibition: None,
        }
    }

    /// Create an invalid enum error.
    pub fn invalid_enum(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::InvalidEnum,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Create a type mismatch error.
    pub fn type_mismatch(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::TypeMismatch,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Create a constraint violation error.
    pub fn constraint_violation(
        field_path: impl Into<String>,
        expected: impl Into<String>,
        got: impl Into<String>,
        next_actions: Vec<String>,
    ) -> Self {
        Self {
            code: ErrorCode::ConstraintViolation,
            field_path: field_path.into(),
            expected: expected.into(),
            got: Some(got.into()),
            next_actions,
            prohibition: None,
        }
    }

    /// Add a prohibition to this error.
    pub fn with_prohibition(mut self, prohibition: impl Into<String>) -> Self {
        self.prohibition = Some(prohibition.into());
        self
    }
}

#[cfg(test)]
mod error_response_tests {
    use super::*;

    #[test]
    fn error_code_serializes_to_screaming_snake_case() {
        assert_eq!(
            serde_json::to_string(&ErrorCode::MissingField).ok(),
            Some("\"MISSING_FIELD\"".to_string())
        );
        assert_eq!(
            serde_json::to_string(&ErrorCode::InvalidEnum).ok(),
            Some("\"INVALID_ENUM\"".to_string())
        );
        assert_eq!(
            serde_json::to_string(&ErrorCode::TypeMismatch).ok(),
            Some("\"TYPE_MISMATCH\"".to_string())
        );
        assert_eq!(
            serde_json::to_string(&ErrorCode::ConstraintViolation).ok(),
            Some("\"CONSTRAINT_VIOLATION\"".to_string())
        );
    }

    #[test]
    fn error_code_round_trip() {
        for code in [
            ErrorCode::MissingField,
            ErrorCode::InvalidEnum,
            ErrorCode::TypeMismatch,
            ErrorCode::ConstraintViolation,
        ] {
            let json = serde_json::to_string(&code).expect("serialize");
            let deserialized: ErrorCode = serde_json::from_str(&json).expect("deserialize");
            assert_eq!(code, deserialized);
        }
    }

    #[test]
    fn validation_error_serializes_to_directive_format() {
        let error = ValidationError::missing_field(
            "steps[0].title",
            "non-empty string",
            vec!["Set steps[0].title to a non-empty string describing the step".to_string()],
        );

        let json = serde_json::to_value(&error).expect("serialize");
        assert_eq!(json["code"], "MISSING_FIELD");
        assert_eq!(json["field_path"], "steps[0].title");
        assert_eq!(json["expected"], "non-empty string");
        assert!(json["got"].is_null());
        assert_eq!(json["next_actions"].as_array().map(|a| a.len()), Some(1));
        // prohibition should be omitted when None
        assert!(json.get("prohibition").is_none());
    }

    #[test]
    fn validation_error_with_prohibition() {
        let error = ValidationError::invalid_enum(
            "steps[1].step_type",
            "one of: file_change, action, research",
            "update",
            vec!["Set steps[1].step_type to one of: file_change, action, research".to_string()],
        )
        .with_prohibition("Do not use values from the old XSD schema".to_string());

        let json = serde_json::to_value(&error).expect("serialize");
        assert_eq!(
            json["prohibition"],
            "Do not use values from the old XSD schema"
        );
    }

    #[test]
    fn error_response_serializes_complete_structure() {
        let response = ErrorResponse::new(
            "plan",
            vec![
                ValidationError::missing_field(
                    "steps[0].title",
                    "non-empty string",
                    vec!["Provide a non-empty title for step 1".to_string()],
                ),
                ValidationError::type_mismatch(
                    "steps[0].number",
                    "integer",
                    "string \"1\"",
                    vec!["Change steps[0].number from string \"1\" to integer 1".to_string()],
                ),
            ],
        );

        let json = serde_json::to_value(&response).expect("serialize");
        assert_eq!(json["artifact_type"], "plan");
        assert_eq!(json["errors"].as_array().map(|a| a.len()), Some(2));
        assert_eq!(json["errors"][0]["code"], "MISSING_FIELD");
        assert_eq!(json["errors"][1]["code"], "TYPE_MISMATCH");
    }

    #[test]
    fn error_response_round_trip() {
        let response = ErrorResponse::new(
            "plan",
            vec![ValidationError::constraint_violation(
                "summary.scope_items",
                "minItems: 3",
                "0 items",
                vec![
                    "Add at least 3 scope items to summary.scope_items".to_string(),
                    "Each scope item must describe a concrete deliverable".to_string(),
                ],
            )],
        );

        let json_str = serde_json::to_string(&response).expect("serialize");
        let deserialized: ErrorResponse = serde_json::from_str(&json_str).expect("deserialize");
        assert_eq!(response, deserialized);
    }

    #[test]
    fn error_response_converts_to_json_rpc_error() {
        let response = ErrorResponse::new(
            "plan",
            vec![ValidationError::missing_field(
                "steps[0].title",
                "non-empty string",
                vec!["Set steps[0].title to a non-empty string".to_string()],
            )],
        );

        let rpc_error = response.into_json_rpc_error();
        assert_eq!(rpc_error.code, -32602);
        assert!(rpc_error.message.contains("1 error"));
        assert!(rpc_error.data.is_some());

        let data = rpc_error.data.as_ref().expect("data present");
        assert_eq!(data["artifact_type"], "plan");
    }
}
