//! Memory safety and resource management tests
//!
//! This module verifies that the pipeline does not exhibit unbounded memory growth,
//! resource leaks, or circular reference patterns. It establishes behavioral invariants
//! that must hold for long-running pipelines.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (bounded growth, no hangs, clean shutdown)
//! - Uses `MemoryWorkspace` and `MockProcessExecutor` for isolation
//! - NO real filesystem, process spawning, or external dependencies
//! - Tests are deterministic and verify invariants, not implementation details

mod bounded_growth;
mod channel_bounds;
mod edge_cases;
mod long_running_pipeline;
mod thread_lifecycle;
mod unsafe_patterns;
