//! MCP server implementation for RFC-009 Phase 3.
//!
//! This module provides the core MCP server that runs alongside each agent
//! process, brokerling all tool calls through Ralph's capability system.
//!
//! # Architecture
//!
//! ```text
//! Agent Process <--JSON-RPC--> Ralph MCP Server <--> AgentSession (capabilities)
//!                                    |
//!                                    +--> ToolRegistry (handler dispatch)
//!                                    |
//!                                    +--> AuditTrail (record all calls)
//! ```
//!
//! # Session Binding
//!
//! The MCP server is created with a reference to an `AgentSession` that
//! defines the capabilities and policy for this agent invocation. Every
//! tool call goes through:
//!
//! 1. Parse JSON-RPC request
//! 2. Check tool exists in registry
//! 3. Check session has required capabilities
//! 4. Execute handler
//! 5. Record audit entry
//! 6. Return result or error

pub(crate) mod audit_adapter;
pub(crate) mod capability_mapping;
pub mod session_bridge;
pub mod startup;
pub mod tool_artifact;
pub(crate) mod tool_bridge;
pub mod tool_coordination;
pub mod tool_exec;
pub mod tool_git_read;
pub mod tool_workspace;

#[cfg(test)]
mod tests;
