//! Domain layer for xtask: pure logic with no side effects.
//!
//! This module contains business rules, parsing, planning, interpretation,
//! and other pure functions. No I/O, no process spawning, no environment
//! access.

pub mod compliance;
pub mod main_policy;
pub mod tailwind_policy;
