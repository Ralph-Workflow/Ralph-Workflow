//! TOML error parsing and user-friendly formatting.
//!
//! This module transforms cryptic TOML deserialization errors into
//! clear, actionable error messages for users.

/// Extract the key name from a TOML deserialization error.
///
/// Parses toml error messages that look like:
/// - "missing field `developer_iters` at line 5"
/// - "invalid type: string \"five\", expected u32 for field `developer_iters`"
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
pub fn extract_key_from_toml_error(error: &str) -> String {
    if let Some(start) = error.find('`') {
        if let Some(end) = error[start + 1..].find('`') {
            return error[start + 1..start + 1 + end].to_string();
        }
    }
    "unknown".to_string()
}

/// Format an invalid type error message.
///
/// Transforms TOML's verbose error messages into clear, concise explanations.
///
/// # Examples
///
/// Input: "invalid type: string \"five\", expected u32 for field `developer_iters`"
/// Output: "Expected u32, got string \"five\""
pub fn format_invalid_type_message(error: &str) -> String {
    // Parse the toml error to extract expected vs actual types
    // Format: "invalid type: string \"five\", expected u32 for field `developer_iters`"
    if error.contains("invalid type") {
        if let Some(start) = error.find("invalid type: ") {
            let rest = &error[start + 13..];
            if let Some(comma) = rest.find(',') {
                let actual = &rest[..comma];
                if let Some(expected_start) = rest.find("expected ") {
                    let expected_part = &rest[expected_start + 9..];
                    if let Some(end) = expected_part.find(' ') {
                        return format!("Expected {}, got {}", &expected_part[..end], actual);
                    }
                }
                return format!("Invalid value: {actual}");
            }
        }
    }
    error.to_string()
}
