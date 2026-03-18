//! Text Cleaning Functions for LLM Output
//!
//! This module provides functions to clean and filter extracted LLM output,
//! removing AI thought processes, formatted thinking patterns, and other artifacts.

/// Unescape common JSON escape sequences in text.
///
/// This handles cases where LLMs output content with JSON escapes (like `\n` for newline)
/// that weren't properly decoded before being used as commit messages.
///
/// This is needed because some agents output JSON string values with escape sequences
/// that leak through when the JSON is parsed but not fully unescaped.
///
/// # Examples
///
/// ```
/// # use ralph_workflow::files::llm_output_extraction::cleaning::unescape_json_strings;
/// let input = "feat: add feature\\n\\nThis adds new functionality.";
/// let result = unescape_json_strings(input);
/// assert_eq!(result, "feat: add feature\n\nThis adds new functionality.");
/// ```
#[must_use]
pub fn unescape_json_strings(content: &str) -> String {
    if content.contains("\\n") || content.contains("\\t") || content.contains("\\r") {
        content
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\r", "\r")
    } else {
        content.to_string()
    }
}

/// Aggressively unescape all JSON escape sequences, including multiple passes.
///
/// This function is more aggressive than `unescape_json_strings()` and performs
/// multiple passes to catch escape sequences that may be embedded in different ways.
///
/// This is used as a final cleanup step to ensure no escape sequences leak through.
///
/// # Examples
///
/// ```
/// # use ralph_workflow::files::llm_output_extraction::cleaning::unescape_json_strings_aggressive;
/// let input = "feat: add feature\\\\n\\\\nDouble escaped";
/// let result = unescape_json_strings_aggressive(input);
/// assert_eq!(result, "feat: add feature\n\nDouble escaped");
/// ```
#[must_use]
pub fn unescape_json_strings_aggressive(content: &str) -> String {
    std::iter::successors(Some(content.to_string()), |prev| {
        let next = prev
            .replace("\\\\n", "\n")
            .replace("\\\\t", "\t")
            .replace("\\\\r", "\r")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\r", "\r");
        (next != *prev).then_some(next)
    })
    .last()
    .unwrap_or_default()
}

/// Check if content contains literal escape sequences that indicate improper unescaping.
///
/// Returns true if the content contains patterns like `\n`, `\t`, `\r` that suggest
/// JSON escape sequences were not properly converted to actual characters.
///
/// This is used to detect cases where unescaping failed and we need to apply it again.
#[must_use]
pub fn contains_literal_escape_sequences(content: &str) -> bool {
    content.lines().enumerate().any(|(i, line)| {
        let trimmed = line.trim();

        // Check for body starting with literal escape sequences (after subject line)
        // Pattern: "feat: add\n\\n\\nBody text" - the second line is literally "\n\n"
        if i == 1 && (trimmed == "\\n" || trimmed == "\\n\\n" || trimmed.starts_with("\\n\\n")) {
            return true;
        }

        // Check for repeated escape sequences that suggest bulk unescaping failure
        // Pattern: "\\n\\n\\n" or "\\n\\n\\n\\n" - multiple escaped newlines
        trimmed.contains("\\n\\n\\n") || trimmed.contains("\\n\\n\\n\\n")
    })
}

/// Apply final post-processing to ensure no escape sequences remain in commit message.
///
/// This is called as the last step before returning a commit message to ensure
/// any escape sequences that leaked through the pipeline are caught and fixed.
///
/// Returns the cleaned commit message.
#[must_use]
pub fn final_escape_sequence_cleanup(message: &str) -> String {
    if contains_literal_escape_sequences(message) {
        unescape_json_strings_aggressive(message)
    } else {
        unescape_json_strings(message)
    }
}

/// Pre-process raw log content by applying aggressive escape sequence unescaping.
///
/// This is the FIRST transformation applied to raw log content to handle cases where
/// agents output JSON with improperly escaped strings. This handles:
/// - Single-escaped: \n -> newline
/// - Double-escaped: \\n -> newline
/// - Triple-escaped: \\\n -> backslash + newline
///
/// The function is idempotent - calling it multiple times produces the same result.
#[must_use]
pub fn preprocess_raw_content(content: &str) -> String {
    std::iter::successors(Some(content.to_string()), |prev| {
        let next = prev
            .replace("\\\\n", "\x00NEWLINE\x00")
            .replace("\\n", "\n")
            .replace("\x00NEWLINE\x00", "\n")
            .replace("\\\\t", "\x00TAB\x00")
            .replace("\\t", "\t")
            .replace("\x00TAB\x00", "\t")
            .replace("\\\\r", "\x00CR\x00")
            .replace("\\r", "\r")
            .replace("\x00CR\x00", "\r");
        (next != *prev).then_some(next)
    })
    .last()
    .unwrap_or_default()
}

#[cfg(test)]
include!("cleaning/test_helpers_thought_stripping.rs");

#[cfg(test)]
include!("cleaning/test_helpers_formatting.rs");

#[cfg(test)]
mod cleaning_tests;
