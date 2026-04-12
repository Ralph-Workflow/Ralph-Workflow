//! MCP protocol type definitions.
//!
//! This module contains pure data types for JSON-RPC communication. These types
//! have no side effects and are serializable for wire encoding.
//!
//! # Module Structure
//!
//! - [`types`] - All protocol types: JSON-RPC envelopes, tool definitions, error types
//!
//! # Protocol Overview
//!
//! The MCP protocol uses JSON-RPC 2.0 with Content-Length framing. Each message
//! is preceded by a header: `Content-Length: <bytes>\r\n\r\n`
//!
//! ## Connection Lifecycle
//!
//! 1. Client connects (via stdio or TCP loopback endpoint)
//! 2. Client sends `initialize` request
//! 3. Server responds with `InitializeResult` containing capabilities
//! 4. Client sends `ping` or tool requests
//! 5. Server processes and responds
//! 6. Session continues until disconnect or `ping` failure
//!
//! ## Capability Gating
//!
//! Every tool invocation is gated by session capabilities. The server checks
//! `session.check_capability(required_capability)` before executing. If the
//! session lacks the required capability, the tool returns a capability error.

pub mod types;

pub use types::{
    ClientCapabilities, ClientInfo, ErrorCode, ErrorResponse, InitializeParams, InitializeResult,
    JsonRpcError, JsonRpcRequest, JsonRpcResponse, NullResult, ServerCapabilities, ServerInfo,
    ToolContent, ToolDefinition, ToolResult, ToolsCapability, ValidationError,
    MCP_PROTOCOL_VERSION,
};
