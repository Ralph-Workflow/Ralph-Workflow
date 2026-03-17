//! Runtime boundary module.
//!
//! Handles OS-facing capabilities: processes, environment, time, terminal.
//! All code in this module may use mutation, imperative loops, and I/O.
//!
//! Command construction and result interpretation belong in domain code.
//!
//! ## Module Organization
//!
//! This module provides thin wrappers around OS capabilities.
//! Pure business logic should live in domain modules.
//!
//! ## Key Principles
//!
//! - Use traits to abstract OS capabilities for testing
//! - Keep this module focused on execution, not business decisions
//! - Return raw results, let domain code interpret them

pub mod clock;
pub mod environment;
pub mod process_executor;
pub mod streaming;
pub mod terminal;
