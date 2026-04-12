//! Illegal character validation for XML content.
//!
//! **CRITICAL:** ALL XML validators MUST call `check_for_illegal_xml_characters()` BEFORE parsing.
//!
//! ## Why This Is Mandatory
//!
//! XML 1.0 allows only specific characters:
//! - Tab (0x09), Line Feed (0x0A), Carriage Return (0x0D)
//! - Characters from 0x20 to 0xD7FF
//! - Characters from 0xE000 to 0xFFFD
//! - Characters from 0x10000 to 0x10FFFF
//!
//! When illegal characters (like NUL bytes) are present:
//! 1. XML parsers produce cryptic error messages
//! 2. XSD retry spins without converging
//! 3. Agents cannot understand what to fix
//!
//! This validator provides clear, actionable error messages that enable
//! XSD retry to converge successfully.
//!
//! ## Common Mistake
//!
//! Writing `\u0000` (NUL) instead of `\u00A0` (non-breaking space).
//! The validator detects this and suggests the NBSP fix.
//!
//! ## Usage Example
//!
//! See the unit tests in this module for working examples.

use crate::common::truncate_text;
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};

/// Check for illegal XML 1.0 characters in content.
///
/// Returns `Ok(())` if content is valid, or an error with detailed position,
/// context, and suggestions if illegal characters are found.
///
/// # Examples
///
/// See the unit tests in this module for working examples.
pub(crate) fn check_for_illegal_xml_characters(content: &str) -> Result<(), XsdValidationError> {
    if let Some((byte_index, ch)) = content.char_indices().find(|&(_, ch)| {
        matches!(
            ch as u32,
            0x00                // NUL byte
            | 0x01..=0x08      // Control characters
            | 0x0B | 0x0C      // Vertical tab, form feed
            | 0x0E..=0x1F      // Other control characters
            | 0xD800..=0xDFFF  // UTF-16 surrogates
            | 0xFFFE | 0xFFFF  // Non-characters
        )
    }) {
        Err(illegal_character_error(ch, byte_index, content))
    } else {
        Ok(())
    }
}

/// Create a detailed error message for an illegal XML character.
///
/// The error includes:
/// - Character description (e.g., "NUL (null byte)" or "control character 0x0B")
/// - Position in the content
/// - Surrounding context (up to 100 characters)
/// - Actionable suggestion for fixing
fn illegal_character_error(ch: char, byte_index: usize, content: &str) -> XsdValidationError {
    let char_display = match ch {
        '\0' => "NUL (null byte)".to_string(),
        '\u{0001}'..='\u{001F}' => format!("control character 0x{:02X}", ch as u32),
        _ => format!("0x{:04X}", ch as u32),
    };

    // Extract context around the error position (50 chars before, 50 chars after)
    let context_start = byte_index.saturating_sub(50);
    let context_end = (byte_index + 50).min(content.len());
    let safe_start = floor_char_boundary(content, context_start);
    let safe_end = ceil_char_boundary(content, context_end.max(safe_start));
    let error_context = content.get(safe_start..safe_end).unwrap_or(content);
    let preview = truncate_text(error_context, 100);

    // Provide specific suggestions based on character type
    let suggestion = if ch == '\0' {
        format!(
            "NUL byte found at position {byte_index}. Common causes:\n\
             - Intended to use non-breaking space (\\u00A0) but wrote \\u0000 instead\n\
             - Binary data mixed into text content\n\
             - Incorrect escape sequence\n\n\
             Near: {preview}"
        )
    } else {
        format!(
            "Illegal character {char_display} found at position {byte_index}. Options to fix:\n\
             - Remove the illegal character\n\
             - Replace with a valid character (e.g., space or \u{00A0})\n\n\
             Near: {preview}"
        )
    };

    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "xml".to_string(),
        expected: "valid XML 1.0 content (no illegal control characters)".to_string(),
        found: format!("illegal character {char_display} at byte position {byte_index}"),
        suggestion,
        example: None,
    }
}

/// Find the nearest character boundary at or before the given index.
fn floor_char_boundary(content: &str, index: usize) -> usize {
    if index >= content.len() {
        return content.len();
    }

    content
        .char_indices()
        .map(|(pos, _)| pos)
        .take_while(|&pos| pos <= index)
        .last()
        .unwrap_or(0)
}

/// Find the nearest character boundary at or after the given index.
fn ceil_char_boundary(content: &str, index: usize) -> usize {
    if index >= content.len() {
        return content.len();
    }

    content
        .char_indices()
        .map(|(pos, _)| pos)
        .find(|&pos| pos >= index)
        .unwrap_or(content.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_check_for_illegal_xml_characters_accepts_valid_content() {
        // Valid content with allowed characters
        let valid = "Hello world\nNew line\tTab\rCarriage return";
        assert!(
            check_for_illegal_xml_characters(valid).is_ok(),
            "Valid content should pass"
        );
    }

    #[test]
    fn test_check_for_illegal_xml_characters_accepts_unicode() {
        // Valid Unicode characters
        let valid = "Hello 世界 🌍 Ωμέγα";
        assert!(
            check_for_illegal_xml_characters(valid).is_ok(),
            "Valid Unicode should pass"
        );
    }

    #[test]
    fn test_check_for_illegal_xml_characters_rejects_nul() {
        // NUL byte (the bug from the issue report)
        let invalid = "text\0here";
        let result = check_for_illegal_xml_characters(invalid);
        assert!(result.is_err(), "NUL byte should be rejected");

        let error = result.unwrap_err();
        assert!(
            error.found.contains("NUL") || error.found.contains("0x00"),
            "Error should mention NUL or 0x00, got: {}",
            error.found
        );
        assert!(
            error.suggestion.contains("\\u00A0") || error.suggestion.contains("non-breaking space"),
            "Error should suggest NBSP as common fix, got: {}",
            error.suggestion
        );
    }

    #[test]
    fn test_check_for_illegal_xml_characters_rejects_control_chars() {
        // Various control characters
        let test_cases = vec![
            ("\u{0001}", "0x01"),
            ("\u{0008}", "0x08"),
            ("\u{000B}", "0x0B"), // Vertical tab
            ("\u{000C}", "0x0C"), // Form feed
            ("\u{000E}", "0x0E"),
            ("\u{001F}", "0x1F"),
        ];

        for (invalid_str, expected_code) in test_cases {
            let content = format!("text{invalid_str}here");
            let result = check_for_illegal_xml_characters(&content);
            assert!(
                result.is_err(),
                "Control character {expected_code} should be rejected"
            );

            let error = result.unwrap_err();
            assert!(
                error.found.contains(expected_code) || error.found.contains("control character"),
                "Error should mention control character, got: {}",
                error.found
            );
        }
    }

    #[test]
    fn test_illegal_character_error_does_not_suggest_cdata_for_control_chars() {
        let invalid = "text\u{0001}here";
        let result = check_for_illegal_xml_characters(invalid);
        assert!(result.is_err(), "Control character should be rejected");

        let error = result.unwrap_err();
        assert!(
            !error.suggestion.contains("CDATA"),
            "Control character suggestions should not mention CDATA, got: {}",
            error.suggestion
        );
    }

    #[test]
    fn test_check_for_illegal_xml_characters_provides_context() {
        // Position and context information
        let invalid = "Valid text before\0invalid character after";
        let result = check_for_illegal_xml_characters(invalid);
        assert!(result.is_err());

        let error = result.unwrap_err();
        // Should include surrounding context
        assert!(
            error.suggestion.contains("before") || error.suggestion.contains("after"),
            "Error should include context, got: {}",
            error.suggestion
        );
        // Should mention position
        assert!(
            error.found.contains("position"),
            "Error should mention position, got: {}",
            error.found
        );
    }

    #[test]
    fn test_illegal_character_error_handles_multibyte_context_without_panic() {
        let mut prefix = String::from("aaaaaaaaa");
        prefix.push('é');
        let remaining = 60 - prefix.len();
        prefix.push_str(&"b".repeat(remaining));
        assert_eq!(prefix.len(), 60);

        let content = format!("{prefix}\0tail");

        let result = std::panic::catch_unwind(|| check_for_illegal_xml_characters(&content));
        assert!(result.is_ok(), "Should not panic on multibyte boundaries");

        let error = result.unwrap().unwrap_err();
        assert!(
            error.suggestion.contains("Near:"),
            "Error should include context preview, got: {}",
            error.suggestion
        );
    }

    #[test]
    fn test_check_for_illegal_xml_characters_allows_tab_newline_cr() {
        // These control characters ARE allowed in XML
        let valid = "text\twith\ntab\rand\nnewlines";
        assert!(
            check_for_illegal_xml_characters(valid).is_ok(),
            "Tab, LF, CR should be allowed"
        );
    }

    #[test]
    fn test_illegal_character_error_format_is_actionable() {
        // Verify error format is suitable for AI retry
        let invalid = "git\0diff";
        let result = check_for_illegal_xml_characters(invalid);
        assert!(result.is_err());

        let error = result.unwrap_err();
        let formatted = error.format_for_ai_retry();

        // Should contain key information for agent to fix
        assert!(
            formatted.contains("NUL") || formatted.contains("0x00"),
            "Formatted error should mention NUL"
        );
        assert!(
            formatted.contains("How to fix") || formatted.contains("suggestion"),
            "Formatted error should include fix guidance"
        );
    }
}
