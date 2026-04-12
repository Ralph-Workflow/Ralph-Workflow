//! MCP server startup backwards compatibility module.
//!
//! This module is kept for backwards compatibility but is now empty.
//! The actual implementation lives in `crate::mcp_server::startup` (a boundary module).
//!
//! # Migration
//!
//! Callers should import from `crate::mcp_server::startup` directly:
//! - `crate::mcp_server::startup::start_mcp_server_for_session`
//! - `crate::mcp_server::startup::access_mode_for_drain`

// This module intentionally left empty.
// The pub mod declaration remains so that any stale import paths produce a
// compile error rather than silently returning nothing.