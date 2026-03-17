//! Redaction utilities for cloud-mode logging/payloads.
//!
//! Cloud mode must never log or report secrets. Git and HTTP error strings can
//! contain embedded credentials (for example, URLs with `user:pass@host`).
//!
//! This module provides a conservative sanitizer for untrusted error strings.

/// Redact likely secrets from an untrusted, user-controlled string.
///
/// This is intentionally conservative. It may redact non-secret strings if they
/// resemble tokens.
#[must_use]
pub fn redact_secrets(input: &str) -> String {
    let mut s = input.to_string();
    s = redact_http_url_userinfo(&s);
    s = redact_common_query_params(&s);
    s = redact_bearer_tokens(&s);
    s = redact_token_like_substrings(&s);
    truncate_redacted(&s)
}

fn truncate_redacted(input: &str) -> String {
    const MAX_LEN: usize = 4096;

    if input.len() <= MAX_LEN {
        return input.to_string();
    }

    let mut out = input[..MAX_LEN].to_string();
    out.push_str("...<truncated>");
    out
}

fn redact_http_url_userinfo(input: &str) -> String {
    // Replace `http(s)://user[:pass]@host` with `http(s)://<redacted>@host`.
    // This is conservative: we only redact when an '@' appears in the URL authority.
    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0;

    while i < bytes.len() {
        let rest = &input[i..];
        let (scheme, scheme_len) = if rest.starts_with("https://") {
            ("https://", 8usize)
        } else if rest.starts_with("http://") {
            ("http://", 7usize)
        } else {
            out.push(bytes[i] as char);
            i = i.saturating_add(1);
            continue;
        };

        // Copy the scheme.
        out.push_str(scheme);
        let scheme_end = i + scheme_len;

        // URL authority ends at '/' or whitespace (or end-of-string).
        let mut end = scheme_end;
        while end < bytes.len() {
            let b = bytes[end];
            if b == b'/' || b.is_ascii_whitespace() {
                break;
            }
            end = end.saturating_add(1);
        }

        let authority = &input[scheme_end..end];
        if let Some(at_pos) = authority.rfind('@') {
            // Keep only the host portion after the last '@'.
            out.push_str("<redacted>@");
            out.push_str(&authority[at_pos + 1..]);
        } else {
            out.push_str(authority);
        }

        // Continue copying the remainder (including the slash/whitespace that stopped us).
        i = end;
    }

    out
}

fn redact_bearer_tokens(input: &str) -> String {
    // Replace `Bearer <token>` with `Bearer <redacted>` (case-insensitive match on "bearer").
    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let rest = &input[i..];
        if starts_with_ignore_ascii_case(rest, "bearer ") {
            out.push_str("Bearer ");
            out.push_str("<redacted>");
            i += "bearer ".len();
            // Skip token characters (up to whitespace).
            while i < bytes.len() && !bytes[i].is_ascii_whitespace() {
                i = i.saturating_add(1);
            }
            continue;
        }

        out.push(bytes[i] as char);
        i = i.saturating_add(1);
    }
    out
}

fn redact_common_query_params(input: &str) -> String {
    // Redact common credential-bearing query params and key/value fragments.
    // We intentionally handle both '&' separated and whitespace terminated values.
    const KEYS: [&str; 5] = [
        "access_token=",
        "token=",
        "password=",
        "passwd=",
        "oauth_token=",
    ];

    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let mut matched: Option<&'static str> = None;
        for key in KEYS {
            if input[i..].starts_with(key) {
                matched = Some(key);
                break;
            }
        }

        if let Some(key) = matched {
            out.push_str(key);
            out.push_str("<redacted>");
            i += key.len();
            while i < bytes.len() {
                let b = bytes[i];
                if b == b'&' || b.is_ascii_whitespace() {
                    break;
                }
                i = i.saturating_add(1);
            }
            continue;
        }

        out.push(bytes[i] as char);
        i = i.saturating_add(1);
    }

    out
}

fn redact_token_like_substrings(input: &str) -> String {
    // Redact substrings that look like common tokens, even if not in a URL.
    // Examples: GitHub PATs, GitLab PATs, Slack tokens, Google OAuth tokens.
    const PREFIXES: [&str; 6] = ["ghp_", "github_pat_", "glpat-", "xoxb-", "xapp-", "ya29."];

    let mut out = String::with_capacity(input.len());
    let bytes = input.as_bytes();
    let mut i = 0;

    while i < bytes.len() {
        let mut matched_prefix: Option<&'static str> = None;
        for p in PREFIXES {
            if input[i..].starts_with(p) {
                matched_prefix = Some(p);
                break;
            }
        }

        if let Some(prefix) = matched_prefix {
            // Consume token characters.
            let mut end = i + prefix.len();
            while end < bytes.len() {
                let b = bytes[end];
                let c = b as char;
                if c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.' {
                    end = end.saturating_add(1);
                    continue;
                }
                break;
            }

            out.push_str("<redacted>");
            i = end;
            continue;
        }

        out.push(bytes[i] as char);
        i = i.saturating_add(1);
    }

    out
}

fn starts_with_ignore_ascii_case(haystack: &str, needle: &str) -> bool {
    haystack
        .get(0..needle.len())
        .is_some_and(|p| p.eq_ignore_ascii_case(needle))
}

#[cfg(test)]
mod tests {
    use super::redact_secrets;

    #[test]
    fn redacts_http_url_userinfo() {
        let s = "fatal: could not read Username for 'https://token@github.com/org/repo.git': terminal prompts disabled";
        let out = redact_secrets(s);
        assert!(
            !out.contains("token@github.com"),
            "should remove userinfo from URL authority"
        );
        assert!(
            out.contains("https://<redacted>@github.com"),
            "should preserve scheme and host"
        );
    }

    #[test]
    fn redacts_http_url_user_and_password() {
        let s = "remote: https://user:pass@github.com/org/repo.git";
        let out = redact_secrets(s);
        assert!(!out.contains("user:pass@"));
        assert!(out.contains("https://<redacted>@github.com"));
    }

    #[test]
    fn redacts_bearer_tokens() {
        let s = "Authorization: Bearer abcdef123456";
        let out = redact_secrets(s);
        assert_eq!(out, "Authorization: Bearer <redacted>");
    }

    #[test]
    fn redacts_common_query_token_params() {
        let s = "GET /?access_token=abc123&other=ok";
        let out = redact_secrets(s);
        assert!(out.contains("access_token=<redacted>"));
        assert!(out.contains("other=ok"));
    }

    #[test]
    fn redacts_github_like_tokens() {
        let s = "error: ghp_abcdefghijklmnopqrstuvwxyz0123456789";
        let out = redact_secrets(s);
        assert!(!out.contains("ghp_"));
        assert!(out.contains("<redacted>"));
    }

    #[test]
    fn truncates_very_long_messages() {
        let input = "x".repeat(10_000);
        let out = redact_secrets(&input);
        assert!(out.len() < input.len());
        assert!(out.ends_with("...<truncated>"));
    }
}
