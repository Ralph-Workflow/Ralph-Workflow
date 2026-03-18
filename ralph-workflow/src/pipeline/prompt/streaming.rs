//! Streaming I/O for agent output.
//!
//! This module re-exports streaming utilities from the runtime boundary module.

mod error_extraction;

pub use error_extraction::{
    extract_error_identifier_from_logfile, extract_error_message_from_logfile,
    extract_session_id_from_logfile,
};
