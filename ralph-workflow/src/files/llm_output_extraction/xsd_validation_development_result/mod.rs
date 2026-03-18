//! XSD validation for development result XML format.
//!
//! This module provides validation of XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format for development results.
//!
//! Uses `quick_xml` for robust XML parsing with proper whitespace handling.
//!
//! # Module Organization
//!
//! - [`types`]: Type definitions (`DevelopmentResultElements`)
//! - [`validation`]: XML validation logic (`validate_development_result_xml`)

mod types;
mod validation;

#[cfg(test)]
pub use types::DevelopmentResultElements;
pub use validation::validate_continuation_development_result_xml;
pub use validation::validate_development_result_xml;

mod tests;
