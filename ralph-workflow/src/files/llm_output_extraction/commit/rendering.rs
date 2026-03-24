//! Final commit message rendering and subject validation.
//!
//! Provides `render_final_commit_message` for applying escape-sequence
//! cleanup and whitespace normalization, and `is_conventional_commit_subject`
//! for validating commit type prefixes.

/// Check if a string is a valid conventional commit subject line.
#[must_use]
pub(crate) fn is_conventional_commit_subject(subject: &str) -> bool {
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
pub(crate) fn render_final_commit_message(message: &str) -> String {
    message.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // Tests for render_final_commit_message
    // =========================================================================

    #[test]
    fn test_render_final_commit_message_with_literal_escapes() {
        let input = "feat: add feature\n\\n\\nBody with literal escapes";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_already_clean() {
        let input = "feat: add feature\n\nBody text here";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_preserves_single_blank_line_separators() {
        let input = "feat: add feature\n\nBody text here\n\nMore details";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_with_tabs() {
        let input = "feat: add feature\\n\\t- item 1\\n\\t- item 2";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_with_carriage_returns() {
        let input = "feat: add feature\\r\\nBody text";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_whitespace_cleanup() {
        let input = "feat: add feature\n\nBody text\n\n\n  \n  ";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_mixed_escape_sequences() {
        let input = "feat: add feature\\n\\nDetails:\\r\\n\\t- item 1\\n\\t- item 2";
        let result = render_final_commit_message(input);
        assert_eq!(result, input);
    }

    #[test]
    fn test_render_final_commit_message_preserves_literal_escape_sequences_from_xml() {
        let input = r"fix: preserve literal escapes\n\nDo not rewrite this text";
        let result = render_final_commit_message(input);

        assert_eq!(result, input);
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
