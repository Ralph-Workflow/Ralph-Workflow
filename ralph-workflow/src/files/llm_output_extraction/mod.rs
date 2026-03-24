//! LLM Output Extraction Module
//!
//! This module provides robust extraction of structured content from various LLM CLI output formats.
//! It supports multiple parser types and gracefully degrades when encountering unexpected formats.
//!
//! # Supported Formats
//!
//! - **Claude**: NDJSON with `{"type": "result", "result": "..."}` events
//! - **Codex**: NDJSON with `item.completed` events containing `agent_message` items
//! - **Gemini**: NDJSON with `{"type": "result"}` and `{"type": "message"}` events
//! - **`OpenCode`**: NDJSON with `{"type": "text"}` events
//! - **Generic**: Plain text output (fallback)
//!
//! # Design Principles
//!
//! 1. **Always return something**: Even if parsing fails, return the cleaned raw output
//! 2. **Try multiple strategies**: Each format has multiple extraction patterns
//! 3. **Auto-detection**: Can detect format from content if not specified
//! 4. **Validation**: Optional validation for extracted content

pub mod cleaning;
mod commit;
pub mod file_based_extraction;
#[cfg(test)]
mod parsers;
pub mod xml_extraction;
mod xml_extraction_development_result;
mod xml_extraction_fix_result;
mod xml_extraction_issues;
mod xml_extraction_plan;
mod xml_formatter;
pub(crate) mod xml_helpers;
pub(crate) mod xsd_validation;
pub(crate) mod xsd_validation_development_result;
pub(crate) mod xsd_validation_fix_result;
pub(crate) mod xsd_validation_issues;
pub(crate) mod xsd_validation_plan;

// Internal types module (only used for tests)
#[cfg(test)]
mod types;

// Re-export public functions from cleaning module
pub use cleaning::preprocess_raw_content;

// Re-export file-based extraction utilities
// Note: Non-workspace variants are deprecated for pipeline layer code.
// Use the _with_workspace variants instead.
pub use file_based_extraction::{
    archive_xml_file_with_workspace, has_valid_xml_output, paths as xml_paths,
    try_extract_from_file_with_workspace,
};

// NOTE: The deprecated std::fs functions (archive_xml_file, extract_xml_with_file_fallback,
// try_extract_from_file) are NOT re-exported. All callers should use the _with_workspace variants.
// The deprecated functions remain in file_based_extraction.rs for reference but are pub(crate) only.

// Import clean_plain_text for tests
#[cfg(test)]
pub use cleaning::clean_plain_text;

// Re-export public functions from commit module
pub use commit::{try_extract_xml_commit_document_with_trace, CommitExtractionResult};

// Public exports for commit message XML validation
pub use xsd_validation::validate_xml_against_xsd;

// XSD validation is now internal (pub(crate))

// Public exports for plan XML extraction and validation
pub use xml_extraction_plan::extract_plan_xml;
pub use xsd_validation_plan::{validate_plan_xml, McpEntry, PlanElements, SkillEntry, SkillsMcp};

// Public exports for issues XML extraction and validation
pub use xml_extraction_issues::extract_issues_xml;
pub use xsd_validation_issues::{validate_issues_xml, IssueEntry, IssuesElements};

// Public exports for fix result XML extraction and validation (used by fix pass)
pub use xml_extraction_fix_result::extract_fix_result_xml;
pub use xsd_validation_fix_result::validate_fix_result_xml;
#[cfg(test)]
pub use xsd_validation_fix_result::FixResultElements;

// Public exports for development result XML extraction and validation
pub use xml_extraction_development_result::extract_development_result_xml;
pub use xsd_validation_development_result::validate_continuation_development_result_xml;
pub use xsd_validation_development_result::validate_development_result_xml;
pub use xsd_validation_development_result::DevelopmentResultElements;

// Public export for XML formatting
pub use xml_formatter::format_xml_for_display;

#[cfg(test)]
mod tests;
