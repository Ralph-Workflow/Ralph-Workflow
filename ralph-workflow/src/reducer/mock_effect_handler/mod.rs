//! Mock implementation of `EffectHandler` for testing.
//!
//! This module provides a mock handler that captures all executed effects
//! for later inspection, returning appropriate mock `PipelineEvents` without
//! performing any real side effects (no git calls, no file I/O, no agent execution).
//!
//! ## Purpose
//!
//! `MockEffectHandler` is critical test infrastructure for the Ralph pipeline.
//! It allows testing reducer logic, orchestration decisions, and event loop behavior
//! without requiring actual agent execution, git operations, or filesystem access.
//!
//! ## Architecture Role
//!
//! In the reducer architecture:
//! - **Reducers** (pure) transform state based on events
//! - **Orchestrators** (pure) derive effects from state
//! - **Handlers** (impure) execute effects and produce events
//!
//! `MockEffectHandler` replaces the real handler in tests, capturing effects
//! and returning deterministic mock events. This enables:
//! 1. Fast, hermetic unit tests
//! 2. Verification of effect sequences
//! 3. Testing error paths without real failures
//! 4. Checkpoint/resume testing without real agent execution
//!
//! ## Usage
//!
//! ```ignore
//! use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
//! use ralph_workflow::reducer::{Effect, EffectHandler, PipelineState};
//!
//! // Create mock with initial state
//! let state = PipelineState::initial(1, 0);
//! let mut handler = MockEffectHandler::new(state);
//!
//! // Configure mock behavior (optional)
//! handler = handler.with_empty_diff(); // Simulate empty commit scenario
//!
//! // Execute effects - no real side effects occur
//! let result = handler.execute(Effect::CreateCommit {
//!     message: "test".to_string()
//! }, &mut ctx)?;
//!
//! // Verify effect was captured
//! assert!(handler.was_effect_executed(|e|
//!     matches!(e, Effect::CreateCommit { .. })
//! ));
//!
//! // Verify expected events were emitted
//! assert!(matches!(result.event, PipelineEvent::CommitCreated { .. }));
//! ```
//!
//! ## Module Organization
//!
//! - [`core`] - `MockEffectHandler` struct, builder methods, and inspection helpers
//! - [`effect_mapping`] - Effect-to-event mapping logic (pure `execute_mock` method)
//! - [`handler`] - `EffectHandler` and `StatefulHandler` trait implementations
//! - [`tests`] - Test suite for mock handler behavior
//!
//! ## Design Principles
//!
//! 1. **Deterministic**: Same effect always produces same event
//! 2. **Hermetic**: No external dependencies or side effects
//! 3. **Fast**: In-memory only, no I/O delays
//! 4. **Observable**: Captures all effects and UI events for verification
//! 5. **Flexible**: Builder pattern for configuring mock scenarios
//!
//! ## See Also
//!
//! - `reducer::effect` - Effect types this handler executes
//! - `reducer::event` - Event types this handler produces
//! - `app::event_loop` - Event loop that uses this handler in tests

#![cfg(any(test, feature = "test-utils"))]

use super::effect::{Effect, EffectHandler, EffectResult};
use super::event::PipelineEvent;
use super::state::PipelineState;
use super::ui_event::UIEvent;
use crate::phases::PhaseContext;
use anyhow::Result;

mod core;
mod effect_mapping;
mod handler;
mod io;

// Re-export the main type
pub use core::MockEffectHandler;

#[cfg(test)]
mod tests;
