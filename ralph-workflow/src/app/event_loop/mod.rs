//! Event loop for reducer-based pipeline architecture.
//!
//! This module is now a re-export module for backward compatibility.
//! The actual implementation is in the `app::core` module.

// Re-export StatefulHandler from core
pub use crate::app::config::EventLoopConfig;
pub use crate::app::core::run_event_loop_with_handler;
pub use crate::app::core::StatefulHandler;
