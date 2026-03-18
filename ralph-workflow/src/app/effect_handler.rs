//! Re-export of AppEffectHandler from the I/O boundary module.
//!
//! This module re-exports the production handler for `AppEffect` execution.
//! The implementation has been moved to `app/io/effect_handler.rs` to satisfy
//! the boundary module requirements for filesystem and environment operations.

pub use super::io::effect_handler::RealAppEffectHandler;
