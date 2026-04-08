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
    ///
    /// Output format (dumb-agent-proof):
    /// - What failed: one sentence plain language
    /// - Where it failed: exact element/path/tag
    /// - How to fix: ordered checklist
    /// - Do not do: anti-actions list
    pub fn format_for_ai_retry(&self) -> String {
        match self.error_type {
            XsdErrorType::MissingRequiredElement => {
                let how_to_fix = vec![
                    format!("Add the missing <{}> element", self.element_path),
                    self.suggestion.clone(),
                ];
                let machine_details = vec![
                    format!("Expected: {}", self.expected),
                    format!("Found: {}", self.found),
                ];
                format_retry_message(
                    &format!("Missing required element '{}'.", self.element_path),
                    &self.element_path,
                    &how_to_fix,
                    &[
                        "Do not add any new content beyond fixing the XML structure",
                        "Do not redo implementation or planning work",
                        "Do not modify any files except the XML output",
                    ],
                    &machine_details,
                    self.example.as_deref(),
                )
            }
            XsdErrorType::UnexpectedElement => {
                let how_to_fix = vec![
                    format!(
                        "Remove or correct the unexpected element at {}",
                        self.element_path
                    ),
                    self.suggestion.clone(),
                ];
                let machine_details = vec![
                    format!("Expected: {}", self.expected),
                    format!("Found: {}", self.found),
                ];
                format_retry_message(
                    &format!("Unexpected element '{}' found.", self.element_path),
                    &self.element_path,
                    &how_to_fix,
                    &[
                        "Do not add any new content beyond fixing the XML structure",
                        "Do not redo implementation or planning work",
                        "Do not modify any files except the XML output",
                    ],
                    &machine_details,
                    self.example.as_deref(),
                )
            }
            XsdErrorType::InvalidContent => {
                let how_to_fix = vec![
                    format!(
                        "Fix the content at {} to meet requirements",
                        self.element_path
                    ),
                    self.suggestion.clone(),
                ];
                let machine_details = vec![
                    format!("Expected: {}", self.expected),
                    format!("Found: {}", self.found),
                ];
                format_retry_message(
                    &format!("Invalid content in element '{}'.", self.element_path),
                    &self.element_path,
                    &how_to_fix,
                    &[
                        "Do not add any new content beyond fixing the XML structure",
                        "Do not redo implementation or planning work",
                        "Do not modify any files except the XML output",
                    ],
                    &machine_details,
                    self.example.as_deref(),
                )
            }
            XsdErrorType::MalformedXml => {
                let is_illegal_char = self.found.contains("illegal character")
                    || self.found.contains("NUL")
                    || self.found.contains("0x00")
                    || self.found.contains("control character");

                if is_illegal_char {
                    let how_to_fix = vec![
                        "Remove or replace the illegal character".to_string(),
                        "Common cause: \\u0000 (NUL byte) used instead of \\u00A0 (non-breaking space)".to_string(),
                        "For code with special chars, use CDATA sections".to_string(),
                        self.suggestion.clone(),
                    ];
                    let machine_details = vec![
                        format!("Expected: {}", self.expected),
                        format!("Found: {}", self.found),
                    ];
                    format_retry_message(
                        "XML contains illegal character (NOT allowed in XML 1.0).",
                        &self.element_path,
                        &how_to_fix,
                        &[
                            "Do not copy binary data into XML text",
                            "Do not use literal control characters in code examples",
                            "Do not redo implementation or planning work",
                        ],
                        &machine_details,
                        self.example.as_deref(),
                    )
                } else {
                    let how_to_fix = vec![
                        "Fix the XML structure first - ensure proper opening/closing tags"
                            .to_string(),
                        "Check for unclosed tags or mismatched element names".to_string(),
                        "Verify XML declaration is properly formatted".to_string(),
                        self.suggestion.clone(),
                    ];
                    let machine_details = vec![
                        format!("Expected: {}", self.expected),
                        format!("Found: {}", self.found),
                    ];
                    format_retry_message(
                        "XML is malformed and cannot be parsed.",
                        &format!("{} (root structure)", self.element_path),
                        &how_to_fix,
                        &[
                            "Do not add any new content beyond fixing the XML structure",
                            "Do not redo implementation or planning work",
                            "Do not modify any files except the XML output",
                        ],
                        &machine_details,
                        self.example.as_deref(),
                    )
                }
            }
        }
    }
}

fn format_retry_message(
    what_failed: &str,
    where_failed: &str,
    how_to_fix: &[String],
    do_not_do: &[&str],
    machine_details: &[String],
    example: Option<&str>,
) -> String {
    let how_block = how_to_fix
        .iter()
        .enumerate()
        .map(|(idx, step)| format!("{}. {step}", idx + 1))
        .collect::<Vec<_>>()
        .join("\n");

    let example_section = example.map(|example_text| {
        vec![
            String::new(),
            "Example of correct format:".to_string(),
            example_text.to_string(),
        ]
    });

    let do_not_section = {
        let items: String = do_not_do
            .iter()
            .map(|item| format!("- {item}"))
            .collect::<Vec<_>>()
            .join("\n");
        format!("Do not do:\n{items}")
    };

    let machine_section: Option<String> = if machine_details.is_empty() {
        None
    } else {
        let details: String = machine_details
            .iter()
            .map(|detail| format!("- {detail}"))
            .collect::<Vec<_>>()
            .join("\n");
        Some(format!("Machine details:\n{details}"))
    };

    std::iter::empty()
        .chain(std::iter::once(format!("What failed: {what_failed}")))
        .chain(std::iter::once(String::new()))
        .chain(std::iter::once(format!("Where it failed: {where_failed}")))
        .chain(std::iter::once(String::new()))
        .chain(std::iter::once(format!("How to fix:\n{how_block}")))
        .chain(example_section.into_iter().flatten())
        .chain(std::iter::once(String::new()))
        .chain(std::iter::once(do_not_section))
        .chain(machine_section.into_iter().map(|s| format!("\n{s}")))
        .collect::<Vec<_>>()
        .join("\n")
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
