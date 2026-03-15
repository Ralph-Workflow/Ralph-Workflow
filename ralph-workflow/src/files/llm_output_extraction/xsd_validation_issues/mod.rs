//! XSD validation for issues XML format.
//!
//! This module provides validation of XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format for review issues.
//!
//! Uses `quick_xml` for robust XML parsing with proper whitespace handling.
//!
//! # Module Organization
//!
//! This module is split into focused submodules:
//!
//! - [`types`]: Type definitions for parsed issues XML (`IssuesElements`)
//! - [`validation`]: XML validation logic and error handling
//!
//! # Usage
//!
//! The main types and functions are re-exported at the parent level for convenience.
//! Use the shorter import path shown below:
//!
//! ```rust
//! use ralph_workflow::files::llm_output_extraction::validate_issues_xml;
//!
//! let xml = r#"<ralph-issues>
//! <ralph-issue>Missing error handling in API endpoint</ralph-issue>
//! </ralph-issues>"#;
//!
//! match validate_issues_xml(xml) {
//!     Ok(elements) => {
//!         println!("Found {} issues", elements.issues.len());
//!         for issue in &elements.issues {
//!             println!("  - {}", issue.text);
//!         }
//!     }
//!     Err(e) => {
//!         eprintln!("Validation error: {}", e);
//!         if let Some(example) = &e.example {
//!             eprintln!("Example: {}", example);
//!         }
//!     }
//! }
//! ```

pub mod types;
pub mod validation;

// Re-export main types and functions for convenience
pub use types::{IssueEntry, IssuesElements};
pub use validation::validate_issues_xml;

#[cfg(test)]
mod tests;
