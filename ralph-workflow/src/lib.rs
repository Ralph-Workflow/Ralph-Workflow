// DO NOT CHANGE THESE CLIPPY SETTINGS, YOU MUST REFACTOR INSTEAD, EVEN IF IT TAKES YOU 100 YEARS
// Note: unsafe_code is not denied in lib.rs because the library requires unsafe blocks for
// legitimate POSIX operations (fcntl, kill, setpgid, etc.) with proper safety documentation.
// The binary target (main.rs) denies unsafe_code since it should contain no unsafe operations.
//
// Note: clippy::cargo is not enabled because it flags transitive dependency version conflicts
// (e.g., bitflags 1.3.2 from inotify vs 2.10.0 from other crates) which are ecosystem-level
// issues outside our control and don't reflect code quality problems.
#![deny(warnings, clippy::all, clippy::pedantic, clippy::nursery)]
#![cfg_attr(test, allow(clippy::large_stack_frames))]
//! Ralph workflow library for AI agent orchestration.
//!
//! This crate provides the core functionality for the `ralph` CLI binary,
//! implementing a reducer-based architecture for orchestrating AI coding agents
//! through development and review cycles.
//!
//! # Quick Start
//!
//! Ralph is primarily a CLI binary. For library use (integration testing):
//!
//! ```toml
//! [dev-dependencies]
//! ralph-workflow = { version = "0.6", features = ["test-utils"] }
//! ```
//!
//! # Architecture
//!
//! Ralph uses an **event-sourced reducer architecture**. The core state machine
//! follows the pattern:
//!
//! ```text
//! State → Orchestrator → Effect → Handler → Event → Reducer → State
//! ```
//!
//! | Component | Pure? | Role |
//! |-----------|-------|------|
//! | [`reducer::PipelineState`] | Yes | Immutable progress snapshot, doubles as checkpoint |
//! | [`reducer::reduce`] | Yes | `(State, Event) → State` |
//! | [`reducer::determine_next_effect`] | Yes | `State → Effect` |
//! | [`reducer::EffectHandler`] | No | Executes effects, produces events |
//!
//! Business logic lives in reducers (pure). I/O lives in handlers (impure).
//!
//! ## Two Effect Layers
//!
//! Ralph has two distinct effect types for different pipeline stages:
//!
//! | Layer | Module | When | Filesystem Access |
//! |-------|--------|------|-------------------|
//! | `AppEffect` | [`app`] | Before repo root known | `std::fs` directly |
//! | `Effect` | [`reducer`] | After repo root known | Via [`workspace::Workspace`] |
//!
//! These layers must never mix: `AppEffect` cannot use `Workspace`, and `Effect`
//! cannot use `std::fs` directly.
//!
//! # I/O Abstractions
//!
//! All I/O is abstracted through traits for testability:
//!
//! - [`workspace::Workspace`] - Filesystem operations
//!   - Production: [`workspace::WorkspaceFs`]
//!   - Tests: `MemoryWorkspace` (with `test-utils` feature)
//! - [`ProcessExecutor`] - Process spawning
//!   - Production: [`RealProcessExecutor`]
//!   - Tests: `MockProcessExecutor` (with `test-utils` feature)
//!
//! # Feature Flags
//!
//! - `monitoring` (default) - Enable streaming metrics and debugging APIs
//! - `test-utils` - Enable test utilities (`MockProcessExecutor`, `MemoryWorkspace`, etc.)
//! - `hardened-resume` (default) - Enable checkpoint file state capture for recovery
//!
//! # Key Modules
//!
//! **Core Architecture:**
//! - [`reducer`] - Core state machine with pure reducers and effect handlers
//! - [`app`] - CLI layer operating before repo root is known (`AppEffect`)
//! - [`phases`] - Pipeline phase implementations (planning, development, review, commit)
//!
//! **I/O Abstractions:**
//! - [`workspace`] - Filesystem abstraction (`WorkspaceFs` production, `MemoryWorkspace` testing)
//! - [`executor`] - Process execution abstraction for agent spawning
//!
//! **Agent Infrastructure:**
//! - [`agents`] - Agent configuration, registry, and CCS (Claude Code Switch) support
//! - [`json_parser`] - NDJSON streaming parsers for Claude, Codex, Gemini, `OpenCode`
//! - [`prompts`] - Template system for agent prompts
//!
//! **Supporting:**
//! - [`git_helpers`] - Git operations using libgit2 (no CLI dependency)
//! - [`checkpoint`] - Pipeline state persistence for `--resume` support
//! - [`config`] - Configuration loading and verbosity levels
//!
//! # Error Handling
//!
//! Most functions return `anyhow::Result` for flexible error handling with context.
//! Use `.context()` to add context to errors as they propagate.

pub mod agents;
pub mod app;
pub mod banner;
pub mod checkpoint;
pub mod cli;
pub mod cloud;
pub mod common;
pub mod config;
pub mod diagnostics;
pub mod executor;
pub mod exit_pause;
pub mod files;
pub mod git_helpers;
pub mod guidelines;
pub mod interrupt;
pub mod json_parser;
pub mod language_detector;
pub mod logger;
pub mod logging;
pub mod monitoring;
pub mod phases;
pub mod pipeline;
pub mod platform;
pub mod prompts;
pub mod reducer;
pub mod rendering;
pub mod review_metrics;
pub mod templates;
pub mod workspace;

// Benchmarks module - contains public baselines used by integration tests.
// Benchmark *tests* inside the module remain `#[cfg(test)]`.
pub mod benchmarks;

// Re-export XML extraction and validation functions for use in integration tests.
// These functions parse and validate XML output from agent responses (plan, issues, fix results).
pub use files::llm_output_extraction::extract_development_result_xml;
pub use files::llm_output_extraction::extract_fix_result_xml;
pub use files::llm_output_extraction::extract_issues_xml;
pub use files::llm_output_extraction::validate_continuation_development_result_xml;
pub use files::llm_output_extraction::validate_development_result_xml;
pub use files::llm_output_extraction::validate_fix_result_xml;
pub use files::llm_output_extraction::validate_issues_xml;
pub use files::llm_output_extraction::validate_plan_xml;
pub use files::llm_output_extraction::validate_xml_against_xsd;

// Re-export process executor types for dependency injection.
// See [`executor`] module for documentation.
pub use executor::{
    AgentChild, AgentChildHandle, AgentCommandResult, AgentSpawnConfig, ChildProcessInfo,
    ProcessExecutor, ProcessOutput, RealAgentChild, RealProcessExecutor,
};

// Re-export mock executor for test-utils feature.
// Use MockProcessExecutor to control process behavior in integration tests.
#[cfg(any(test, feature = "test-utils"))]
pub use executor::{MockAgentChild, MockProcessExecutor};
