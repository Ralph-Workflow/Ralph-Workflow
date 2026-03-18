//! Common utility functions.
//!
//! This module provides utility functions for command-line interface operations:
//! - Shell command parsing
//! - Text truncation for display
//! - Secret redaction for logging

use std::io;

use regex::Regex;

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
pub fn split_command(cmd: &str) -> io::Result<Vec<String>> {
    let cmd = cmd.trim();
    if cmd.is_empty() {
        return Ok(vec![]);
    }

    shell_words::split(cmd).map_err(|err| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("Failed to parse command string: {err}"),
        )
    })
}

fn secret_like_regex() -> Option<Regex> {
    // Fixed ReDoS vulnerability by:
    // 1. Using \b (word boundary) anchors to prevent overlapping matches
    // 2. Making patterns more specific with exact length ranges
    // 3. Limiting max character class repetition to 100
    Regex::new(
        r"(?ix)
        \b(
          # OpenAI API keys
          sk-[a-z0-9]{20,100} |
          # GitHub tokens
          ghp_[a-z0-9]{20,100} |
          github_pat_[a-z0-9_]{20,100} |
          # Slack tokens
          xox[baprs]-[a-z0-9-]{10,100} |
          # AWS access keys
          AKIA[0-9A-Z]{16} |
          # AWS session tokens
          (?:Aws)?[A-Z0-9]{40,100} |
          # Stripe keys
          sk_live_[a-zA-Z0-9]{24,100} |
          sk_test_[a-zA-Z0-9]{24,100} |
          # Firebase tokens
          [a-zA-Z0-9_/+-]{40,100}\.firebaseio\.com |
          [a-z0-9:_-]{40,100}@apps\.googleusercontent\.com |
          # Generic JWT patterns
          ey[a-zA-Z0-9_-]{1,100}\.[a-zA-Z0-9_-]{1,100}\.[a-zA-Z0-9_-]{1,100}
        )\b
        ",
    )
    .ok()
}

fn is_sensitive_key(key: &str) -> bool {
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

fn redact_arg_value(key: &str, value: &str) -> String {
    if is_sensitive_key(key) {
        return "<redacted>".to_string();
    }
    secret_like_regex().map_or_else(
        || value.to_string(),
        |re| re.replace_all(value, "<redacted>").to_string(),
    )
}

fn shell_quote_for_log(arg: &str) -> String {
    if arg.is_empty() {
        return "''".to_string();
    }
    if !arg
        .chars()
        .any(|c| c.is_whitespace() || matches!(c, '"' | '\'' | '\\'))
    {
        return arg.to_string();
    }
    let escaped = arg.replace('\'', r#"'\"'\"'"#);
    format!("'{escaped}'")
}

/// Format argv for logs, redacting likely secrets.
pub fn format_argv_for_log(argv: &[String]) -> String {
    // Use indices to track state across iterations - simpler than scan for this use case
    let indices = 0..argv.len();
    let out: Vec<String> = indices
        .map(|i| {
            let arg = &argv[i];
            // Check if previous arg was a sensitive key that should trigger redaction
            let prev_was_sensitive = i > 0 && is_sensitive_key(&argv[i - 1]);

            if prev_was_sensitive {
                return "<redacted>".to_string();
            }

            if let Some((k, v)) = arg.split_once('=') {
                // Flag-style (--token=...) or env-style (GITHUB_TOKEN=...)
                let env_key = k.to_ascii_uppercase();
                let looks_like_secret_env = env_key.contains("TOKEN")
                    || env_key.contains("SECRET")
                    || env_key.contains("PASSWORD")
                    || env_key.contains("PASS")
                    || env_key.contains("KEY");
                if is_sensitive_key(k) || looks_like_secret_env {
                    return format!("{}=<redacted>", shell_quote_for_log(k));
                }
                let redacted = redact_arg_value(k, v);
                return shell_quote_for_log(&format!("{k}={redacted}"));
            }

            if is_sensitive_key(arg) {
                // Return as-is, next iteration will redact
                return arg.to_string();
            }

            let redacted = secret_like_regex().map_or_else(
                || arg.clone(),
                |re| re.replace_all(arg, "<redacted>").to_string(),
            );
            shell_quote_for_log(&redacted)
        })
        .collect();

    out.join(" ")
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
