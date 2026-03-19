//! # Review Prompt Preparation Tests
//!
//! Tests for the review phase prompt preparation handler, covering error handling,
//! diff fallback scenarios, retry behavior, and template rendering.
//!
//! ## Coverage Areas
//!
//! - **Error Handling**: Tests for unmaterialized inputs, read failures, and error propagation
//! - **Diff Handling**: Tests for missing DIFF.backup, oversized diffs, and baseline fallback
//! - **Retry Behavior**: Tests for same-agent retry prompt reuse and retry note handling
//! - **Template Rendering**: Tests for template expansion, placeholder handling, and normal mode
//!
//! ## Test Infrastructure
//!
//! The `helpers` module provides test workspace implementations that simulate various
//! failure scenarios for comprehensive error handling coverage.

mod diff_handling;
mod error_handling;
pub(super) mod helpers;
mod retry_behavior;
mod template_rendering;
