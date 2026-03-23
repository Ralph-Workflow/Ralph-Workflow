//! Bounded event queue with semaphore-based backpressure for streaming.
//!
//! This module provides a bounded channel-based queue that sits between the
//! line reader and parser, providing backpressure to prevent memory exhaustion
//! when events are produced faster than they can be consumed.
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
//! │ Line Reader     │  send   │  Bounded Queue   │  recv   │  Parser         │
//! │ (Producer)      │────────▶│  (sync_channel)  │────────▶│  (Consumer)     │
//! └─────────────────┘         └──────────────────┘         └─────────────────┘
//!                                      │
//!                                      │ blocks when full
//!                                      ▼
//!                              backpressure on producer
//! ```
//!
//! # Behavior
//!
//! - **Bounded**: Queue has a fixed size (configurable via env var)
//! - **Blocking**: Producer blocks when queue is full (semaphore behavior)
//! - **Non-blocking fallback**: If queue is full and non-blocking send is attempted,
//!   returns error immediately without dropping data
//!
//! # Configuration
//!
//! Environment variables:
//! - `RALPH_STREAMING_QUEUE_SIZE`: Queue capacity (default: 100, range: 10-1000)
//!
//! # Production Status
//!
//! **This module is test-only (`#[cfg(test)]`) and is not used in production builds.**
//!
//! ## Why Not Production?
//!
//! The current streaming architecture uses **incremental byte-level parsing** which processes
//! events immediately without buffering. This provides:
//! - Zero latency (events processed as soon as JSON is complete)
//! - Bounded memory usage (only the incremental parser buffer)
//! - Immediate deduplication (KMP + Rolling Hash algorithms)
//!
//! A queue would add latency (~10-100ms) without solving an actual problem, as:
//! - The parser is faster than the producer (no backpressure needed)
//! - Memory usage is already bounded (no buffering of unprocessed events)
//! - Deduplication is stateless (no need for event queuing)
//!
//! See `QUEUE_INTEGRATION_ANALYSIS.md` for a detailed analysis of why the queue doesn't
//! fit the current architecture.
//!
//! ## When Would This Be Useful?
//!
//! This queue implementation is kept for:
//! - Future use if the architecture changes to line-based parsing
//! - Testing scenarios that require bounded queuing
//! - Reference implementation for backpressure handling
//!
//! If you need to integrate this queue into production, consider:
//! 1. Whether the incremental parser should be replaced with line-based parsing
//! 2. Whether the latency impact is acceptable for your use case
//! 3. Whether there's an actual performance problem the queue would solve
//!
//! ## Test-Only Implementation
//!
//! The module is conditionally compiled with `#[cfg(test)]` to avoid dead code warnings
//! in production builds. To test queue behavior, use the test suite:
//!
//! ```bash
//! cargo test queue --lib
//! ```

include!("event_queue/config.rs");

pub(super) mod boundary;

include!("event_queue/bounded_queue.rs");

include!("event_queue/tests.rs");
