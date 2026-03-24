//! Common utility functions.
//!
//! This module provides utility functions for command-line interface operations:
//! - Shell command parsing
//! - Text truncation for display
//! - Secret redaction for logging

/// Split a shell-like command string into argv parts.
///
/// Supports quotes and backslash escapes (e.g. `cmd --flag "a b"`).
///
/// # Example
///
/// ```ignore
/// let argv = split_command("echo 'hello world'")?;
/// assert_eq!(argv, vec!["echo", "hello world"]);
/// ```
///
/// # Errors
///
/// Returns an error if the command string has unmatched quotes.
pub fn split_command(cmd: &str) -> std::io::Result<Vec<String>> {
    let cmd = cmd.trim();
    if cmd.is_empty() {
        return Ok(vec![]);
    }

    shell_words::split(cmd).map_err(|err| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("Failed to parse command string: {err}"),
        )
    })
}

pub(crate) fn is_sensitive_key(key: &str) -> bool {
    let key = key.trim().trim_start_matches('-').trim_start_matches('-');
    let key = key
        .split_once('=')
        .or_else(|| key.split_once(':'))
        .map_or(key, |(k, _)| k)
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");

    matches!(
        key.as_str(),
        "token"
            | "access-token"
            | "api-key"
            | "apikey"
            | "auth"
            | "authorization"
            | "bearer"
            | "client-secret"
            | "password"
            | "pass"
            | "passwd"
            | "private-key"
            | "secret"
    )
}

/// Format argv for logs, redacting likely secrets.
///
/// This delegates to the boundary module which handles regex-based redaction.
pub fn format_argv_for_log(argv: &[String]) -> String {
    crate::common::io::format_argv_for_log(argv)
}

/// Truncate text to a limit with ellipsis.
///
/// Uses character count rather than byte length to avoid panics on UTF-8 text.
/// Truncates at character boundaries and appends "..." when truncation occurs.
///
/// # Example
///
/// ```ignore
/// assert_eq!(truncate_text("hello world", 8), "hello...");
/// assert_eq!(truncate_text("short", 10), "short");
/// ```
#[must_use]
pub fn truncate_text(text: &str, limit: usize) -> String {
    // Handle edge case where limit is too small for even "..."
    if limit <= 3 {
        return text.chars().take(limit).collect();
    }

    let char_count = text.chars().count();
    if char_count <= limit {
        text.to_string()
    } else {
        // Leave room for "..."
        let truncate_at = limit.saturating_sub(3);
        let truncated: String = text.chars().take(truncate_at).collect();
        format!("{truncated}...")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_split_command_simple() {
        let result = split_command("echo hello").unwrap();
        assert_eq!(result, vec!["echo", "hello"]);
    }

    #[test]
    fn test_split_command_with_quotes() {
        let result = split_command("echo 'hello world'").unwrap();
        assert_eq!(result, vec!["echo", "hello world"]);
    }

    #[test]
    fn test_split_command_empty() {
        let result = split_command("").unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_split_command_whitespace() {
        let result = split_command("   ").unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_truncate_text_no_truncation() {
        assert_eq!(truncate_text("hello", 10), "hello");
        assert_eq!(truncate_text("hello", 5), "hello");
    }

    #[test]
    fn test_truncate_text_with_ellipsis() {
        // "hello world" is 11 chars, limit 8 means 5 chars + "..."
        assert_eq!(truncate_text("hello world", 8), "hello...");
    }

    #[test]
    fn test_truncate_text_unicode() {
        // Should not panic on UTF-8 multibyte characters
        let text = "日本語テスト"; // 6 Japanese characters
        assert_eq!(truncate_text(text, 10), "日本語テスト");
        assert_eq!(truncate_text(text, 6), "日本語テスト");
        assert_eq!(truncate_text(text, 5), "日本...");
    }

    #[test]
    fn test_truncate_text_emoji() {
        // Emojis can be multi-byte but should be handled correctly
        let text = "Hello 👋 World";
        assert_eq!(truncate_text(text, 20), "Hello 👋 World");
        assert_eq!(truncate_text(text, 10), "Hello 👋...");
    }

    #[test]
    fn test_truncate_text_edge_cases() {
        assert_eq!(truncate_text("abc", 3), "abc");
        assert_eq!(truncate_text("abcd", 3), "abc"); // limit too small for ellipsis
        assert_eq!(truncate_text("ab", 1), "a");
        assert_eq!(truncate_text("", 5), "");
    }

    #[test]
    fn test_truncate_text_cjk_characters() {
        // Each CJK character is 3 bytes in UTF-8
        // This test ensures we truncate by character count, not byte count
        let text = "日本語テスト"; // 6 CJK characters (18 bytes)
                                   // limit=4 means 1 char + "..." (can't fit more)
        assert_eq!(truncate_text(text, 4), "日...");
        // Verify the original 6 char string fits in limit=6
        assert_eq!(truncate_text(text, 6), "日本語テスト");
    }

    #[test]
    fn test_truncate_text_mixed_multibyte() {
        // Mix of single-byte ASCII and multi-byte characters
        let text = "Hello 世界 test"; // 13 chars: "Hello " (6) + "世界" (2) + " test" (5)
        assert_eq!(truncate_text(text, 20), "Hello 世界 test");
        // limit=10: 7 chars + "..."
        assert_eq!(truncate_text(text, 10), "Hello 世...");
    }

    #[test]
    fn test_truncate_text_exact_boundary() {
        // Truncation right at a multi-byte char boundary
        let text = "ab日cd"; // 5 chars: 'a'(1) + 'b'(1) + '日'(3bytes, 1char) + 'c'(1) + 'd'(1)
                             // limit=5: fits exactly 5 chars, no truncation
        assert_eq!(truncate_text(text, 5), "ab日cd");
        // limit=4: 1 char + "..." = "a..."
        assert_eq!(truncate_text(text, 4), "a...");
    }

    #[test]
    fn test_truncate_text_error_message_style() {
        // Test style used in stderr preview (simulating 500 char limit for long content)
        let text = "Error: ".to_string() + &"日".repeat(200);
        let result = truncate_text(&text, 50);
        assert!(result.ends_with("..."), "Result should end with '...'");
        // Character count should be <= 50
        assert!(
            result.chars().count() <= 50,
            "Result char count {} exceeds limit 50",
            result.chars().count()
        );
    }

    #[test]
    fn test_truncate_text_4byte_emoji() {
        // Emoji like 🎉 is 4 bytes in UTF-8 but 1 character
        let text = "🎉🎊🎈"; // 3 emojis = 3 chars (12 bytes total)
        assert_eq!(truncate_text(text, 3), "🎉🎊🎈"); // fits exactly in 3 chars
        assert_eq!(truncate_text(text, 4), "🎉🎊🎈"); // 4 chars > 3 chars, no truncation
                                                      // truncate_text uses chars not bytes, so 3 emojis = 3 chars
                                                      // limit=5 means no truncation for 3 chars
        assert_eq!(truncate_text(text, 5), "🎉🎊🎈");
        // For truncation: need limit < char_count
        // 3 chars, limit 2: can fit 0 chars + "..." (limit too small), so no ellipsis
        assert_eq!(truncate_text(text, 2), "🎉🎊");
    }

    #[test]
    fn test_truncate_text_combining_characters() {
        // Test with combining characters (e.g., é as e + combining accent)
        // Note: "é" can be 1 char (precomposed) or 2 chars (decomposed)
        let text = "cafe\u{0301}"; // café with combining accent (5 chars including combiner)
        let result = truncate_text(text, 10);
        assert_eq!(result, "cafe\u{0301}"); // should fit without truncation
    }
}
