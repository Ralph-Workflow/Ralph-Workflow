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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_valid_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert!(elements.is_completed());
        assert!(!elements.is_partial());
        assert!(!elements.is_failed());
    }

    #[test]
    fn test_validate_valid_partial() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started fixing bugs</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "partial");
        assert!(elements.is_partial());
    }

    #[test]
    fn test_validate_valid_failed() {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Could not complete the task</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "failed");
        assert!(elements.is_failed());
    }

    #[test]
    fn test_validate_valid_with_all_optional_fields() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented feature X</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/utils.rs</ralph-files-changed>
<ralph-next-steps>Continue with testing</ralph-next-steps>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Implemented feature X");
        assert!(elements.files_changed.is_some());
        assert!(elements.files_changed.as_ref().unwrap().contains("main.rs"));
        assert_eq!(
            elements.next_steps,
            Some("Continue with testing".to_string())
        );
    }

    #[test]
    fn test_validate_missing_root_element() {
        let xml = r"Some random text without proper XML tags";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert_eq!(error.element_path, "ralph-development-result");
    }

    #[test]
    fn test_validate_missing_status() {
        let xml = r"<ralph-development-result>
<ralph-summary>No status</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-status"));
    }

    #[test]
    fn test_validate_missing_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-summary"));
    }

    #[test]
    fn test_validate_invalid_status() {
        let xml = r"<ralph-development-result>
<ralph-status>invalid_status_value</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.expected.contains("completed"));
    }

    #[test]
    fn test_validate_empty_status() {
        let xml = r"<ralph-development-result>
<ralph-status>   </ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_empty_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>   </ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_duplicate_status() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-status>partial</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_unexpected_element() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
<ralph-unknown>value</ralph-unknown>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-unknown"));
    }

    #[test]
    fn test_validate_whitespace_handling() {
        // This is the key test - quick_xml should handle whitespace between elements
        let xml = "  <ralph-development-result>  \n  <ralph-status>completed</ralph-status>  \n  <ralph-summary>Test</ralph-summary>  \n  </ralph-development-result>  ";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_with_xml_declaration() {
        let xml = r#"<?xml version="1.0"?>
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>"#;

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_cdata_wrapped_xml() {
        let xml = r#"<![CDATA[<?xml version="1.0"?>
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>]]>"#;

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }
}
