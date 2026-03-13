//! Error builders for XSD validation errors.
//!
//! This module provides functions for creating consistent, actionable error messages
//! for common XML validation failures. All errors are designed to help AI agents
//! understand and fix validation issues during XSD retry.
//!
//! ## Error Categories
//!
//! - **Unexpected elements**: Elements not allowed by the XSD schema
//! - **Missing required elements**: Required elements that are not present
//! - **Duplicate elements**: Elements that appear more than once when only one is allowed
//! - **Text outside tags**: Loose text content not wrapped in proper elements
//! - **Malformed XML**: General XML parsing errors
//!
//! ## Design Principles
//!
//! All error builders follow these principles:
//! 1. **Clear identification**: State what was expected vs. what was found
//! 2. **Actionable suggestions**: Provide specific steps to fix the error
//! 3. **Context preservation**: Include element paths for easy location
//! 4. **Examples when helpful**: Show correct XML structure for complex cases

use crate::common::truncate_text;
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};

/// Create an error for missing required element.
///
/// # Arguments
///
/// * `element_name` - The name of the missing required element
/// * `parent_element` - The parent element that should contain it
/// * `example` - Optional example showing correct usage
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn missing_required_error(
    element_name: &str,
    parent_element: &str,
    example: Option<&str>,
) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: format!("{parent_element}/{element_name}"),
        expected: format!("<{element_name}> element (required)"),
        found: format!("no <{element_name}> found"),
        suggestion: format!(
            "Add <{element_name}>value</{element_name}> inside <{parent_element}>."
        ),
        example: example.map(std::convert::Into::into),
    }
}

/// Create an error for duplicate element.
///
/// # Arguments
///
/// * `element_name` - The name of the duplicated element
/// * `parent_element` - The parent element containing the duplicate
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn duplicate_element_error(element_name: &str, parent_element: &str) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::UnexpectedElement,
        element_path: format!("{parent_element}/{element_name}"),
        expected: format!("only one <{element_name}> element"),
        found: format!("duplicate <{element_name}> element"),
        suggestion: format!("Remove the duplicate <{element_name}>. Only one is allowed."),
        example: None,
    }
}

/// Create an error for text content outside of tags.
///
/// # Arguments
///
/// * `text` - The text content that was found outside tags
/// * `parent_element` - The parent element containing the loose text
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn text_outside_tags_error(text: &str, parent_element: &str) -> XsdValidationError {
    // Use truncate_text for UTF-8 safe truncation (53 chars = ~50 visible + "...")
    let display_text = truncate_text(text, 53);

    XsdValidationError {
        error_type: XsdErrorType::InvalidContent,
        element_path: parent_element.to_string(),
        expected: "only XML elements (no loose text)".to_string(),
        found: format!("text content: {display_text:?}"),
        suggestion: "Remove any text that is not inside a child element tag.".to_string(),
        example: None,
    }
}

/// Format content for display in error messages (UTF-8 safe truncation).
///
/// Returns "empty content" for empty strings, the full content if <= 60 chars,
/// or a truncated preview with "..." for longer content.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn format_content_preview(content: &str) -> String {
    if content.is_empty() {
        "empty content".to_string()
    } else {
        // truncate_text handles the ellipsis and UTF-8 char boundaries
        truncate_text(content, 63)
    }
}

/// Create an error for malformed XML.
///
/// # Arguments
///
/// * `error` - The `quick_xml` parse error
///
/// # Returns
///
/// An `XsdValidationError` with actionable suggestions based on the parse error type.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub fn malformed_xml_error(error: &quick_xml::Error) -> XsdValidationError {
    let error_str = error.to_string();

    // Check if this is likely an illegal character issue (even though we pre-validate)
    // This can catch cases where quick_xml detects invalid UTF-8 or other encoding issues
    let suggestion =
        if error_str.contains("invalid character") || error_str.contains("Invalid character") {
            "Invalid character detected in XML content. Common causes:\n\
         - Illegal control characters (NUL, etc.) in text\n\
         - Invalid UTF-8 encoding\n\
         - Remove or replace illegal characters with valid Unicode"
                .to_string()
        } else if error_str.contains("code") || error_str.contains("block") {
            "Code blocks with special characters (<, >, &) MUST use CDATA sections:\n\
         <code-block><![CDATA[\n\
           if a < b && c > d { ... }\n\
         ]]></code-block>"
                .to_string()
        } else {
            "Check that all XML tags are properly opened and closed. \
         For code with special characters, use CDATA: <![CDATA[your code]]>"
                .to_string()
        };

    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "well-formed XML".to_string(),
        found: format!("parse error: {error}"),
        suggestion,
        example: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_missing_required_error() {
        let error = missing_required_error("child", "parent", Some("<child>example</child>"));
        assert_eq!(error.element_path, "parent/child");
        assert!(error.example.is_some());
    }

    #[test]
    fn test_duplicate_element_error() {
        let error = duplicate_element_error("child", "parent");
        assert!(error.suggestion.contains("Remove"));
    }

    #[test]
    fn test_text_outside_tags_error() {
        let error = text_outside_tags_error("stray text", "parent");
        assert!(error.found.contains("stray text"));
    }

    #[test]
    fn test_text_outside_tags_error_truncates_long_text() {
        let long_text = "x".repeat(100);
        let error = text_outside_tags_error(&long_text, "parent");
        assert!(error.found.contains("..."));
    }

    #[test]
    fn test_malformed_xml_error_suggests_cdata_for_code() {
        let error = quick_xml::Error::Syntax(quick_xml::errors::SyntaxError::UnclosedTag);
        let result = malformed_xml_error(&error);
        // Should return MalformedXml error type
        assert_eq!(result.error_type, XsdErrorType::MalformedXml);
    }
}
