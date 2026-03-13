//! XSD validation for commit message XML format.
//!
//! This module provides validation of XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format.
//!
//! Uses `quick_xml` for robust XML parsing with proper whitespace handling.

use crate::files::llm_output_extraction::commit::is_conventional_commit_subject;
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    read_text_until_end, skip_to_end, text_outside_tags_error,
};
use quick_xml::events::Event;

// Error types and formatting
include!("xsd_validation/error_reporting.rs");

// Core validation implementation
include!("xsd_validation/validator.rs");

// Tests
include!("xsd_validation/tests.rs");
