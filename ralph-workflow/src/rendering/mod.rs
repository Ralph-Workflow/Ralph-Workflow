//! Rendering subsystem for user-facing display output.
//!
//! This module provides the single source of truth for all user-facing
//! terminal output formatting. The event loop calls `render_ui_event()`
//! and displays the result.
//!
//! # Architecture
//!
//! ```text
//! UIEvent -> rendering::render_ui_event() -> String -> Logger.info()
//! ```
//!
//! # Separation of Concerns
//!
//! This module is ONLY responsible for formatting. It must NOT:
//! - Read/write files or touch Workspace
//! - Spawn processes or call executors
//! - Decide pipeline control flow or emit/handle `PipelineEvent`
//! - Perform XML extraction from logs (only render already-provided content)
//!
//! # Output Channels
//!
//! Ralph has two distinct output channels:
//!
//! - **UI channel**: Event loop renders `UIEvent` strings via this module
//!   (phase transitions, progress, semantic XML summaries)
//! - **Streaming channel**: NDJSON parsers write incremental output via their
//!   printer abstraction during agent execution (not routed through this module)
//!
//! # Usage
//!
//! ```ignore
//! use ralph_workflow::rendering::render_ui_event;
//!
//! for ui_event in &result.ui_events {
//!     ctx.logger.info(&render_ui_event(ui_event));
//! }
//! ```

pub mod json_pretty;
mod ui_event;
pub mod xml;

pub use ui_event::render_ui_event;
