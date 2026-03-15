//! XSD validation for plan XML format (v2 - Structured).
//!
//! This module provides validation of structured XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format for development plans.
//!
//! The v2 schema enforces:
//! - Quantified scope items (minimum 3)
//! - Explicit step numbers with types and priorities
//! - Rich content elements (tables, code blocks, lists)
//! - Required risk/mitigation pairs
//! - Required verification strategies

use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::collections::HashMap;

// Include schema definitions (type definitions and structures)
include!("schema.rs");

// Include validation logic (parsing helpers and validation functions)
include!("validation.rs");

#[cfg(test)]
mod tests;
