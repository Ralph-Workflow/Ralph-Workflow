//! # mcp-server - MCP Server Implementation for Ralph Workflow Orchestration
//!
//! This crate provides the MCP (Model Context Protocol) server that enables
//! Ralph to communicate with AI agents like Claude Code, Codex, and OpenCode.
//!
//! ## Architecture Overview
//!
//! ```text
//! Agent Process <--JSON-RPC--> mcp-server <--> Ralph Workflow
//!                                           |
//!                                           +--> HostSession (capabilities)
//!                                           |
//!                                           +--> WorkspaceAdapter (file I/O)
//! ```
//!
//! ## Module Organization
//!
//! | Module | Type | Purpose |
//! |--------|------|---------|
//! | [`protocol`] | Pure | JSON-RPC types, capability declarations, tool definitions |
//! | [`io`] | Boundary | Transport framing, socket handling, stdio I/O, McpServer |
//! | [`dispatch`] | Application | Tool registry, handler dispatch, capability gating |
//!
//! ## Boundary Rules
//!
//! - **`protocol/`** - Pure data types, no side effects
//! - **`io/`** - Boundary module, all actual I/O lives here
//! - **`dispatch/`** - Application logic, handlers are pure functions
//!
//! ## RPC Contract
//!
//! See [`protocol`] module for detailed RPC documentation.
//!
//! ### Supported Methods
//!
//! | Method | Description | Capability Required |
//! |--------|-------------|---------------------|
//! | `initialize` | Handshake, exchange capabilities | None |
//! | `ping` | Liveness check | None |
//! | `tools/list` | List available tools | None |
//! | `tools/call` | Invoke a tool | Tool-specific |
//!
//! ### Error Codes
//!
//! | Code | Meaning |
//! |------|---------|
//! | -32700 | Parse error |
//! | -32600 | Invalid request |
//! | -32601 | Method not found |
//! | -32602 | Invalid params |
//! | -32603 | Internal error |
//! | -32000 | Tool error |
//! | -32001 | Not initialized |
//!
//! ## Capability System
//!
//! Tools are gated by capabilities. The [`McpCapability`] enum defines all available
//! capabilities, and [`ToolRegistry`] checks `session.check_capability()` before
//! invoking any tool handler.
//!
//! ### Capability Mutating Flag
//!
//! Every capability has an implicit mutating flag that determines whether tools
//! requiring that capability are allowed in read-only contexts:
//!
//! | Capability | Mutating |
//! |------------|----------|
//! | `WorkspaceRead` | No |
//! | `WorkspaceWriteEphemeral` | Yes |
//! | `WorkspaceWriteTracked` | Yes |
//! | `WorkspaceWriteAny` | Yes |
//! | `GitStatusRead` | No |
//! | `GitWrite` | Yes |
//! | `EnvRead` | No |
//! | `EnvWrite` | Yes |
//! | `ProcessExecBounded` | Yes |
//! | `ProcessExecUnbounded` | Yes |
//! | `ArtifactSubmit` | No |
//! | `RunReportProgress` | No |
//!
//! See [`dispatch::registry::capability_is_mutating`] for the authoritative list.
//!
//! ## Tool Registry
//!
//! Consumers (like ralph-workflow) create a [`ToolRegistry`] by registering
//! tool handlers with their required capabilities:
//!
//! ```ignore
//! use mcp_server::dispatch::{ToolRegistry, McpCapability};
//!
//! let registry = ToolRegistry::new(vec![
//!     (tool_metadata, handler),
//!     // ...
//! ]);
//! ```
//!
//! Each tool's [`dispatch::ToolMetadata`] contains:
//! - `definition`: [`ToolDefinition`] with name, description, input schema
//! - `required_capability`: [`McpCapability`] required to invoke the tool
//! - `is_mutating`: Override for mutating flag (None = derive from capability)
//!
//! ## Creating a Host Implementation
//!
//! To use mcp-server with Ralph workflow, implement the [`dispatch::HostSession`]
//! and [`dispatch::WorkspaceAdapter`] traits:
//!
//! ```ignore
//! use mcp_server::dispatch::{HostSession, WorkspaceAdapter};
//! use mcp_server::{AccessDecision, McpCapability};
//!
//! struct RalphHostSession {
//!     agent_session: AgentSession,
//! }
//!
//! impl HostSession for RalphHostSession {
//!     fn session_id(&self) -> &str {
//!         &self.agent_session.session_id
//!     }
//!     fn check_capability(&self, cap: McpCapability) -> AccessDecision {
//!         self.agent_session.check_capability(cap)
//!     }
//!     // ...
//! }
//! ```

#![deny(warnings)]
#![deny(clippy::all)]
#![deny(missing_docs)]
#![deny(
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    clippy::dbg_macro,
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]

pub mod dispatch;
pub mod io;
pub mod protocol;

// Re-exports from dispatch (pure types)
pub use dispatch::access::{
    AccessDecision, AccessDeniedCode, AuditSink, McpCapability, NoOpAuditSink,
};
pub use dispatch::{DirEntry, HostSession, ToolError, ToolRegistry, WorkspaceAdapter};

// Re-exports from io (boundary types - McpServer lives in io module)
// Note: McpServer and ServerState are available at mcp_server::io::McpServer and mcp_server::io::ServerState

// Re-exports from protocol (pure types)
pub use protocol::{
    ErrorCode, ErrorResponse, InitializeParams, InitializeResult, JsonRpcError, JsonRpcRequest,
    JsonRpcResponse, NullResult, ServerCapabilities, ServerInfo, ToolContent, ToolDefinition,
    ToolResult, ToolsCapability, ValidationError, MCP_PROTOCOL_VERSION,
};
