// Error types and formatting for XSD validation errors.
// This module contains error reporting logic for AI retry prompts.

/// Detailed XSD validation error for reporting to AI agent.
///
/// This error type provides comprehensive information about what went wrong
/// during validation, making it suitable for generating retry prompts that
/// guide the AI agent toward producing valid output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XsdValidationError {
    /// The type of validation error that occurred
    pub(crate) error_type: XsdErrorType,
    /// The path to the element that failed validation
    pub element_path: String,
    /// What was expected at this location
    pub expected: String,
    /// What was actually found
    pub found: String,
    /// Suggestion for fixing the error
    pub suggestion: String,
    /// Optional concrete example of valid XML (boxed to reduce struct size)
    pub example: Option<Box<str>>,
}

impl XsdValidationError {
    /// Format this error for display in logs or retry prompts.
    pub fn format_for_display(&self) -> String {
        let example_section = self
            .example
            .as_ref()
            .map(|ex| format!("\n  Example:\n{ex}"))
            .unwrap_or_default();

        format!(
            "XSD Validation Error [{}]: {}\n  Element: {}\n  Expected: {}\n  Found: {}\n  Suggestion: {}{}",
            self.error_type,
            self.error_type.description(),
            self.element_path,
            self.expected,
            self.found,
            self.suggestion,
            example_section
        )
    }

    /// Format this error as a concise message for AI retry prompt.
    ///
    /// This provides an actionable, human-readable error message that guides
    /// the AI agent toward producing valid XML output.
    pub fn format_for_ai_retry(&self) -> String {
        let example_section = self
            .example
            .as_ref()
            .map(|ex| format!("\n\nExample of correct format:\n{ex}"))
            .unwrap_or_default();

        match self.error_type {
            XsdErrorType::MissingRequiredElement => {
                format!(
                    "MISSING REQUIRED ELEMENT: '{}' is required but was not found.\n\n\
                     What was expected: {}\n\
                     What was found: {}\n\n\
                     How to fix: {}{}",
                    self.element_path, self.expected, self.found, self.suggestion, example_section
                )
            }
            XsdErrorType::UnexpectedElement => {
                format!(
                    "UNEXPECTED ELEMENT: Found '{}' which is not allowed.\n\n\
                     What was expected: {}\n\
                     What was found: {}\n\n\
                     How to fix: {}{}",
                    self.element_path, self.expected, self.found, self.suggestion, example_section
                )
            }
            XsdErrorType::InvalidContent => {
                format!(
                    "INVALID CONTENT: The content of '{}' does not meet requirements.\n\n\
                     What was expected: {}\n\
                     What was found: {}\n\n\
                     How to fix: {}{}",
                    self.element_path, self.expected, self.found, self.suggestion, example_section
                )
            }
            XsdErrorType::MalformedXml => {
                // Check if this is an illegal character error (contains "NUL", "0x00", "control character", etc.)
                let is_illegal_char = self.found.contains("illegal character")
                    || self.found.contains("NUL")
                    || self.found.contains("0x00")
                    || self.found.contains("control character");

                if is_illegal_char {
                    // Emphasize the illegal character error with prominent formatting
                    format!(
                        "*** ILLEGAL CHARACTER IN XML ***\n\n\
                         Your XML contains a character that is not allowed in XML 1.0.\n\n\
                         What was expected: {}\n\
                         What was found: {}\n\n\
                         *** CRITICAL FIX REQUIRED ***\n\
                         How to fix: {}\n\n\
                         Common mistakes:\n\
                         - Writing \\u0000 (NUL byte) instead of \\u00A0 (non-breaking space)\n\
                         - Copy-pasting binary data into XML text\n\
                         - Using literal control characters in code examples\n\n\
                         Fix: Remove or replace the illegal character. \
                         For code examples with special chars, use CDATA sections.{}",
                        self.expected, self.found, self.suggestion, example_section
                    )
                } else {
                    // Standard malformed XML error
                    format!(
                        "MALFORMED XML: The XML structure is invalid.\n\n\
                         What was expected: {}\n\
                         What was found: {}\n\n\
                         How to fix: {}{}",
                        self.expected, self.found, self.suggestion, example_section
                    )
                }
            }
        }
    }
}

impl std::fmt::Display for XsdValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.format_for_display())
    }
}

impl std::error::Error for XsdValidationError {}

/// Type of XSD validation error.
///
/// Each variant represents a different category of validation failure,
/// allowing for targeted error messages and retry strategies.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum XsdErrorType {
    /// A required element is missing from the XML
    MissingRequiredElement,
    /// An unexpected element was found
    UnexpectedElement,
    /// Element content is invalid
    InvalidContent,
    /// The XML is malformed
    MalformedXml,
}

impl XsdErrorType {
    /// Get a human-readable description of this error type.
    pub(crate) const fn description(self) -> &'static str {
        match self {
            Self::MissingRequiredElement => "Missing required element",
            Self::UnexpectedElement => "Unexpected element",
            Self::InvalidContent => "Invalid content",
            Self::MalformedXml => "Malformed XML",
        }
    }
}

impl std::fmt::Display for XsdErrorType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.description())
    }
}
