//! Agent chain state and advancement tests.
//!
//! Tests the `AgentChainState` tracking for ordered fallback lists.
//! Validates chain construction, position advancement, and exhaustion detection.
//!
//! ## Modules
//!
//! - `chain_construction`: Building chains from agent configs
//! - `advancement`: Moving to next agent after failure
//! - `edge_cases`: Empty chains, single-agent chains, exhaustion

mod advancement;
mod chain_construction;
mod edge_cases;
