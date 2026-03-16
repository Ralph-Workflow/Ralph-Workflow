//! Shared `quick_xml` helper utilities for XSD validation.
//!
//! This module provides common parsing functions used across all XSD validators
//! to ensure consistent XML handling with proper whitespace management.
//!
//! # Illegal Character Validation (CRITICAL)
//!
//! **ALL XML validators MUST call `check_for_illegal_xml_characters()` BEFORE parsing.**
//!
//! This is mandatory for all validators because:
//! - It catches illegal XML 1.0 characters (NUL byte, control chars, etc.)
//! - It provides clear, actionable error messages for AI agents
//! - It enables XSD retry to converge instead of spinning with cryptic parse errors
//!
//! Required validation flow for ALL validators:
//! 1. `check_for_illegal_xml_characters()` - MUST be called first
//! 2. `create_reader()` - creates `quick_xml` reader
//! 3. XSD validation - validates structure and content
//!
//! ## XSD Retry Integration
//!
//! When illegal character validation fails:
//! 1. `check_for_illegal_xml_characters()` returns `XsdValidationError` with detailed context
//! 2. The error includes character position, surrounding text, and fix suggestions
//! 3. `format_for_ai_retry()` enhances the error with prominent illegal character warnings
//! 4. XSD retry prompt templates include character validation guidance
//! 5. Agents receive clear, actionable feedback to remove/replace illegal characters
//!
//! This design ensures agents can converge on valid XML even when the initial output
//! contains illegal characters (e.g., from typos like `\u0000` instead of `\u00A0`).
//!
//! Common mistake: Writing `\u0000` (NUL) instead of `\u00A0` (NBSP).
//! The illegal character check detects this and suggests the NBSP fix.
//!
//! # Code Block Content
//!
//! Code blocks containing special characters (`<`, `>`, `&`) MUST use CDATA sections:
//!
//! ```xml
//! <code-block language="rust"><![CDATA[
//! if a < b && c > d {
//!     println!("hello");
//! }
//! ]]></code-block>
//! ```
//!
//! The parser handles CDATA correctly - content is preserved exactly as written.
//!
//! # Module Organization
//!
//! - [`validation`] - Illegal character detection and validation
//! - [`readers`] - XML reading and traversal utilities
//! - [`errors`] - Error builder functions for consistent error messages
//! - [`tolerant_parsing`] - Tolerant enum normalization and synonym tables for lenient validation

pub mod errors;
pub mod readers;
pub mod tolerant_parsing;
pub mod validation;

// Re-export commonly used functions for backward compatibility
pub use errors::{
    duplicate_element_error, format_content_preview, malformed_xml_error, missing_required_error,
    text_outside_tags_error,
};
pub use readers::{
    create_reader, parse_skills_mcp, read_text_until_end, read_text_until_end_fuzzy, skip_to_end,
};
pub use validation::check_for_illegal_xml_characters;
