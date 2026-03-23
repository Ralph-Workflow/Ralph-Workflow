//! Tests for whitespace sanitization utilities.

use super::*;

#[test]
fn test_sanitize_collapses_multiple_newlines() {
    let result = sanitize_for_display("Hello\n\nWorld");
    // Multiple newlines should become a single space
    assert_eq!(result, "Hello World");
}

#[test]
fn test_sanitize_collapses_multiple_spaces() {
    let result = sanitize_for_display("Hello   World");
    assert_eq!(result, "Hello World");
}

#[test]
fn test_sanitize_mixed_whitespace() {
    let result = sanitize_for_display("Hello\n\n  \t\t  World");
    // All whitespace (newlines, spaces, tabs) collapsed to single space
    assert_eq!(result, "Hello World");
}

#[test]
fn test_sanitize_trims_leading_trailing_whitespace() {
    let result = sanitize_for_display("  Hello World  ");
    assert_eq!(result, "Hello World");
}

#[test]
fn test_sanitize_only_whitespace() {
    let result = sanitize_for_display("   \n\n   ");
    // Only whitespace content becomes empty string
    assert_eq!(result, "");
}

#[test]
fn test_sanitize_preserves_single_spaces() {
    let result = sanitize_for_display("Hello World Test");
    assert_eq!(result, "Hello World Test");
}

#[test]
fn test_sanitize_does_not_truncate() {
    // sanitize_for_display no longer truncates - it just sanitizes whitespace
    let long_content = "This is a very long string that should NOT be truncated anymore";
    let result = sanitize_for_display(long_content);
    // Should NOT be truncated
    assert_eq!(result, long_content);
    assert!(!result.contains("..."));
}
