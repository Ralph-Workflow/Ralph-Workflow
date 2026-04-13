//! Continuation state for development and fix iterations.
//!
//! This module contains the [`ContinuationState`] structure and associated logic for managing
//! continuation and retry attempts across development and fix phases.
//!
//! # Overview
//!
//! The continuation system provides three key mechanisms:
//!
//! ## 1. Continuation Context and Budget System
//!
//! When agent work is incomplete (status "partial" or "failed" for development,
//! "`issues_remain`" or "failed" for fix), the system can trigger a **continuation**
//! to let the agent continue work in the same iteration.
//!
//! **Budget limits:**
//! - Development: `max_continue_count` attempts (default 3)
//! - Fix: `max_fix_continue_count` attempts (default 10)
//!
//! **Context tracking:**
//! - Previous status, summary, files changed, next steps
//! - Continuation attempt counter
//! - Context file write/cleanup flags
//!
//! **Budget exhaustion triggers:**
//! - When `continuation_attempt >= max_continue_count`, no more continuations allowed
//! - System proceeds to next phase even if work is incomplete
//!
//! ## 2. Same-Agent Retries
//!
//! Triggered for transient invocation failures (timeout, internal error):
//! - Budget: `max_same_agent_retry_count` (default 2)
//! - Behavior: Re-invoke same agent with reduced scope guidance
//! - Exhaustion: Falls back to next agent in chain
//!
//! ## 3. Loop Detection Mechanism
//!
//! Prevents infinite tight loops by tracking consecutive identical effects:
//!
//! **Effect fingerprinting:**
//! - Each effect execution is fingerprinted (e.g., "InvokeAgent(Developer, continuation=2)")
//! - System tracks `last_effect_kind` and `consecutive_same_effect_count`
//!
//! **Detection threshold:**
//! - `max_consecutive_same_effect` (default 100, see [`DEFAULT_LOOP_DETECTION_THRESHOLD`])
//! - When threshold is exceeded, triggers loop recovery
//!
//! **Recovery behavior:**
//! - Resets continuation state (including loop counters)
//! - Advances to next agent or phase to break the cycle
//!
//! # State Immutability
//!
//! This is **pure state code** with no side effects:
//! - All methods return new `ContinuationState` instances
//! - No I/O operations (filesystem, environment, logging)
//! - No external dependencies beyond serde for serialization
//!
//! # Module Structure
//!
//! - `state`: Core `ContinuationState` struct definition
//! - `budget`: Budget tracking and exhaustion checking
//! - `loop_detection`: Loop detection and effect fingerprinting

mod budget;
mod loop_detection;
mod state;

pub use state::{ContinuationState, DEFAULT_LOOP_DETECTION_THRESHOLD};
