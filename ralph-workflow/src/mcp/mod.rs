//! MCP adapter layer for ralph-workflow.
//!
//! This module bridges ralph-workflow's domain types (`AgentSession`, `Workspace`,
//! `AuditTrail`) to the `mcp-server` crate's adapter traits (`HostSession`,
//! `WorkspaceAdapter`, `AuditSink`).
//!
//! # Dependency Direction
//!
//! ```text
//! ralph-workflow::mcp  →  ralph-workflow::mcp_server  →  mcp-server
//! ```
//!
//! `mcp-server` defines the traits; `mcp_server` sub-module implements them.
//! Startup orchestration lives in `crate::mcp_server::startup` (a boundary module).
//! Callers should import directly from `crate::mcp_server::startup`.
