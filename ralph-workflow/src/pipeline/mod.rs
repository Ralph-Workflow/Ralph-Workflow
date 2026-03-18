//! Pipeline Execution Module
//!
//! This module contains the core pipeline execution infrastructure for running
//! AI agents with real-time output streaming.
//!
//! # Key Types
//!
//! - [`AgentPhaseGuard`] - RAII guard for phase cleanup on success/failure
//! - [`Timer`] - Execution duration tracking with phase support
//! - [`PipelineRuntime`] - Runtime context for agent execution
//!
//! # Features
//!
//! - **Single-attempt execution** - One agent invocation per effect
//! - **Real-time streaming** - Live output from agents during execution
//! - **Log management** - Structured logging to `.agent/logs/`
//!
//! # Module Structure
//!
//! - `types` - Pipeline statistics tracking and RAII guards
//! - [`logfile`] - Unified log file path creation, parsing, and discovery
//! - [`idle_timeout`] - Timeout handling for stuck agents

#![deny(unsafe_code)]

mod clipboard;
pub mod idle_timeout;
pub mod logfile;
mod prompt;
pub mod timer;
mod types;

pub use prompt::{
    extract_error_identifier_from_logfile, extract_error_message_from_logfile, run_with_prompt,
    PipelineRuntime, PromptCommand,
};
pub use timer::Timer;
pub use types::AgentPhaseGuard;

// ===== Tests use the boundary Timer =====

#[cfg(test)]
mod timer_tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn test_format_duration_zero() {
        let d = Duration::from_secs(0);
        assert_eq!(Timer::format_duration(d), "0m 00s");
    }

    #[test]
    fn test_format_duration_seconds() {
        let d = Duration::from_secs(30);
        assert_eq!(Timer::format_duration(d), "0m 30s");
    }

    #[test]
    fn test_format_duration_minutes() {
        let d = Duration::from_secs(65);
        assert_eq!(Timer::format_duration(d), "1m 05s");
    }

    #[test]
    fn test_format_duration_large() {
        let d = Duration::from_secs(3661);
        assert_eq!(Timer::format_duration(d), "61m 01s");
    }
}
