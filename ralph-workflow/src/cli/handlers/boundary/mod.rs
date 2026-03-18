//! CLI boundary module for I/O operations.
//!
//! This module contains CLI handlers that perform console I/O.
//! According to the Boundary-First Architecture pattern, all I/O
//! operations (including console output) should live in boundary modules.
//!
//! See `docs/plans/2026-03-16-functional-rust-refactoring-plan.md` for details.

pub mod diagnose;
pub mod io;
pub mod template_selection;
pub mod terminal;
