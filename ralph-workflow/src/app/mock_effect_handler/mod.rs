//! Mock implementation of `AppEffectHandler` for testing.
//!
//! This module provides a mock handler that captures all executed effects
//! for later inspection, maintains an in-memory filesystem state, and provides
//! builder methods for test configuration.
//!
//! # Two Effect Layers
//!
//! Ralph has two distinct effect layers (see `CODE_STYLE.md)`:
//!
//! - **`AppEffect`** - Used by CLI layer before repository root is known.
//!   Includes `GitRequireRepo`, `PathExists`, `ReadFile`, etc.
//!   This mock handler is for testing these operations.
//!
//! - **Effect** - Used by pipeline layer after repository root is known.
//!   Uses `Workspace` trait for filesystem operations.
//!   See `reducer::mock_effect_handler` for pipeline mocks.
//!
//! # Usage in CLI Tests
//!
//! ```ignore
//! use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
//! use ralph_workflow::app::effect::{AppEffect, AppEffectHandler};
//!
//! let mut handler = MockAppEffectHandler::new()
//!     .with_file("PROMPT.md", "# Task")
//!     .with_head_oid("abc123");
//!
//! // Execute an app-layer effect
//! let result = handler.execute(AppEffect::ReadFile {
//!     path: "PROMPT.md".into(),
//! });
//!
//! // Verify expected effects executed
//! assert!(handler.was_executed(&AppEffect::GitRequireRepo));
//! ```
//!
//! # Module Organization
//!
//! - [`core`] - Core `MockAppEffectHandler` struct and `AppEffectHandler` trait impl
//! - [`app_expectations`] - Builder methods for configuring mock state
//! - [`file_state`] - File management and inspection methods
//!
//! # See Also
//!
//! - `app::effect` - `AppEffect` definitions and real handler
//! - `reducer::mock_effect_handler` - Pipeline-layer mock (uses Workspace)
//! - `docs/architecture/effect-system.md` - Two-layer effect system documentation

#![cfg(any(test, feature = "test-utils"))]

mod app_expectations;
mod core;
mod file_state;

#[cfg(test)]
mod io_tests;

pub use core::MockAppEffectHandler;
