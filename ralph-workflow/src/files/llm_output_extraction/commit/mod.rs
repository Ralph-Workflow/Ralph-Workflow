//! Commit Message Extraction Functions
//!
//! This module provides utilities for extracting commit messages from AI agent output
//! using XML format with XSD validation.
//!
//! # Module Organization
//!
//! - [`extraction`]: `CommitExtractionResult` and `try_extract_xml_commit_with_trace`
//! - [`rendering`]: `render_final_commit_message` and `is_conventional_commit_subject`

mod extraction;
mod rendering;

pub use extraction::{try_extract_xml_commit_with_trace, CommitExtractionResult};
pub use rendering::is_conventional_commit_subject;
