//! MCP server tests.
//!
//! This module organizes MCP server tests into focused test suites:
//! - `tool_tests` - Unit tests for individual tool handlers
//! - `capability_tests` - Tests for capability enforcement per tool
//! - `blacklist_tests` - Tests for command blacklist enforcement
//! - `snapshot_tests` - Snapshot tests for protocol messages
//! - `e2e_socket_behavior` - End-to-end behavioral tests over real Unix sockets
//! - `integration` - Integration-level acceptance tests for adapter wiring

mod blacklist_tests;
mod capability_tests;
mod e2e_socket_behavior;
mod integration;
mod snapshot_tests;
mod tool_tests;
mod validation_error_transport_tests;
