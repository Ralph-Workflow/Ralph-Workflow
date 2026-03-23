//! Agent fallback and retry test scenarios.
//!
//! Tests the reducer's handling of agent fallback chains when agents fail,
//! covering retry logic, chain advancement, and exhaustion conditions.
//!
//! ## Test Organization
//!
//! - `basic_fallback`: Simple fallback to next agent in chain
//! - `retry_scenarios`: Same-agent retry with transient failures  
//! - `chain_exhaustion`: Behavior when all agents exhausted
//! - `state_transitions`: State transitions during fallback

mod basic_fallback;
mod chain_exhaustion;
mod retry_scenarios;
mod state_transitions;
