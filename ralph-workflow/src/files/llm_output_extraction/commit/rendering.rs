//! Final commit message rendering and subject validation.
//!
//! Provides `render_final_commit_message` for applying escape-sequence
//! cleanup and whitespace normalization, and `is_conventional_commit_subject`
//! for validating commit type prefixes.

use crate::files::llm_output_extraction::cleaning::{
    final_escape_sequence_cleanup, unescape_json_strings_aggressive,
};

/// Check if a string is a valid conventional commit subject line.
#[must_use]
pub fn is_conventional_commit_subject(subject: &str) -> bool {
    let valid_types = [
        "feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore",
    ];

    // Find the colon
    let Some(colon_pos) = subject.find(':') else {
        return false;
    };

    let prefix = &subject[..colon_pos];

    // Extract type (before optional scope and !)
    let type_end = prefix
        .find('(')
        .unwrap_or_else(|| prefix.find('!').unwrap_or(prefix.len()));
    let commit_type = &prefix[..type_end];

    valid_types.contains(&commit_type)
}

// =========================================================================
// Final Commit Message Rendering
// =========================================================================

/// Render the final commit message with all cleanup applied.
///
/// This is the final step before returning a commit message for use in git commit.
/// It applies:
/// 1. Escape sequence cleanup (aggressive unescaping)
/// 2. Final whitespace cleanup
///
/// # Arguments
///
/// * `message` - The commit message to render
///
/// # Returns
///
/// The fully rendered commit message with all escape sequences properly handled.
pub fn render_final_commit_message(message: &str) -> String {
    let mut result = message.to_string();

    // Step 1: Apply final escape sequence cleanup
    // This handles any escape sequences that leaked through the pipeline
    result = final_escape_sequence_cleanup(&result);

    // Step 2: Try aggressive unescaping if there are still escape sequences
    if result.contains("\\n") || result.contains("\\t") || result.contains("\\r") {
        result = unescape_json_strings_aggressive(&result);
    }

    // Step 3: Final whitespace cleanup
    //
    // Preserve conventional blank-line separators (e.g. subject/body, body sections)
    // while collapsing runs of blank lines and trimming trailing whitespace.
    let mut out: Vec<String> = Vec::new();
    let mut saw_non_blank = false;
    let mut prev_blank = false;

    for raw in result.lines() {
        let trimmed = raw.trim();
        let is_blank = trimmed.is_empty();

        if !saw_non_blank {
            if is_blank {
                continue;
            }
            out.push(trimmed.to_string());
            saw_non_blank = true;
            prev_blank = false;
            continue;
        }

        if is_blank {
            if prev_blank {
                continue;
            }
            out.push(String::new());
            prev_blank = true;
        } else {
            out.push(trimmed.to_string());
            prev_blank = false;
        }
    }

    while out.last().is_some_and(|l| l.trim().is_empty()) {
        out.pop();
    }

    result = out.join("\n");

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // Tests for render_final_commit_message
    // =========================================================================

    #[test]
    fn test_render_final_commit_message_with_literal_escapes() {
        // Test that render_final_commit_message cleans up escape sequences
        // Note: whitespace cleanup preserves conventional blank-line separators
        let input = "feat: add feature\n\\n\\nBody with literal escapes";
        let result = render_final_commit_message(input);
        assert_eq!(result, "feat: add feature\n\nBody with literal escapes");
    }

    #[test]
    fn test_render_final_commit_message_already_clean() {
        // Test that already-clean messages pass through (whitespace cleanup applied)
        let input = "feat: add feature\n\nBody text here";
        let result = render_final_commit_message(input);
        assert_eq!(result, "feat: add feature\n\nBody text here");
    }

    #[test]
    fn test_render_final_commit_message_preserves_single_blank_line_separators() {
        // Conventional commit messages use a blank line between subject and body.
        let input = "feat: add feature\n\nBody text here\n\nMore details";
        let result = render_final_commit_message(input);
        assert_eq!(
            result,
            "feat: add feature\n\nBody text here\n\nMore details"
        );
    }

    #[test]
    fn test_render_final_commit_message_with_tabs() {
        // Test that tab escapes are properly handled
        let input = "feat: add feature\\n\\t- item 1\\n\\t- item 2";
        let result = render_final_commit_message(input);
        // Tabs are stripped by whitespace cleanup (trim() removes leading whitespace)
        assert_eq!(result, "feat: add feature\n- item 1\n- item 2");
    }

    #[test]
    fn test_render_final_commit_message_with_carriage_returns() {
        // Test that carriage return escapes are properly handled
        let input = "feat: add feature\\r\\nBody text";
        let result = render_final_commit_message(input);
        // Carriage returns are converted, but whitespace cleanup removes extra blank lines
        assert_eq!(result, "feat: add feature\nBody text");
    }

    #[test]
    fn test_render_final_commit_message_whitespace_cleanup() {
        // Test that trailing empty lines are removed
        let input = "feat: add feature\n\nBody text\n\n\n  \n  ";
        let result = render_final_commit_message(input);
        assert_eq!(result, "feat: add feature\n\nBody text");
    }

    #[test]
    fn test_render_final_commit_message_mixed_escape_sequences() {
        // Test handling of mixed escape sequences
        let input = "feat: add feature\\n\\nDetails:\\r\\n\\t- item 1\\n\\t- item 2";
        let result = render_final_commit_message(input);
        // Carriage returns normalized to newlines, tabs stripped by trim, blank line preserved
        assert_eq!(result, "feat: add feature\n\nDetails:\n- item 1\n- item 2");
    }

    // =========================================================================
    // Tests for is_conventional_commit_subject
    // =========================================================================

    #[test]
    fn test_conventional_commit_subject_valid() {
        assert!(is_conventional_commit_subject("feat: add feature"));
        assert!(is_conventional_commit_subject("fix: resolve bug"));
        assert!(is_conventional_commit_subject("docs: update readme"));
        assert!(is_conventional_commit_subject(
            "refactor(core): simplify logic"
        ));
        assert!(is_conventional_commit_subject("feat!: breaking change"));
        assert!(is_conventional_commit_subject("fix(api)!: breaking fix"));
    }

    #[test]
    fn test_conventional_commit_subject_invalid() {
        assert!(!is_conventional_commit_subject("invalid: not a type"));
        assert!(!is_conventional_commit_subject("no colon here"));
        assert!(!is_conventional_commit_subject(""));
        assert!(!is_conventional_commit_subject("Feature: capitalize"));
    }
}
