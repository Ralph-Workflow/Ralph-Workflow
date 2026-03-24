//! Brokered Sessions integration tests.
//!
//! These tests verify that RFC-009 brokered session enforcement works correctly:
//! - Session handshake is recorded at agent invocation start
//! - Audit trail accumulates capability checks during agent invocations
//! - Audit trail is persisted to `.agent/audit/{session_id}.jsonl` after session ends
//! - Capability gates correctly deny effects that require capabilities the session doesn't have
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** via effect capture and workspace file inspection
//! - Uses `MockAppEffectHandler` AND `MockEffectHandler` with `session_override` for capability gate testing
//! - NO `TempDir`, `std::fs`, or real git operations
//! - Tests are deterministic and verify effects, not real filesystem state

pub(crate) mod audit_trail_tests;
pub(crate) mod capability_enforcement_tests;
pub(crate) mod session_handshake_tests;
