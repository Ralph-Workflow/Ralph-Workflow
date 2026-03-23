//! Parser health monitoring and graceful degradation.
//!
//! This module provides utilities for monitoring parser health,
//! tracking parsed vs ignored events, and providing warnings when
//! parsers are not working correctly with specific agents.
//!
//! # Event Classification
//!
//! Events are classified into the following categories:
//!
//! - **Parsed events**: Successfully processed and displayed, including:
//!   - Complete content events
//!   - Successfully handled event types
//!
//! - **Partial events**: Streaming delta events (text deltas, thinking deltas,
//!   tool input deltas) that are displayed incrementally. These are NOT errors
//!   and are tracked separately to show real-time streaming activity without
//!   inflating "ignored" percentages.
//!
//! - **Control events**: State management events that don't produce user-facing
//!   output. These are NOT errors and are tracked separately to avoid inflating
//!   "ignored" percentages. Examples: `MessageStart`, `ContentBlockStart`, `Ping`,
//!   `TurnStarted`, `StepStarted`.
//!
//! - **Unknown events**: Valid JSON that the parser deserializes successfully
//!   but doesn't have specific handling for. These are NOT considered errors
//!   and won't trigger health warnings. They represent future/new event types.
//!
//! - **Parse errors**: Malformed JSON that cannot be deserialized. These DO
//!   trigger health warnings when they exceed 50% of events.
//!
//! - **Ignored events**: General category for events not displayed (includes
//!   both unknown events and parse errors)
//!
//! # Streaming Quality Metrics
//!
//! The [`StreamingQualityMetrics`] struct provides insights into streaming behavior:
//!
//! - **Delta sizes**: Average, min, max delta sizes to understand streaming granularity
//! - **Total deltas**: Count of deltas per content block
//! - **Streaming pattern**: Classification as smooth, bursty, or chunked based on size variance

pub mod metrics;
pub mod monitor;
pub mod parser_health;

pub use metrics::{StreamingPattern, StreamingQualityMetrics};
pub use monitor::HealthMonitor;
pub use parser_health::ParserHealth;

#[cfg(test)]
mod io_tests;
