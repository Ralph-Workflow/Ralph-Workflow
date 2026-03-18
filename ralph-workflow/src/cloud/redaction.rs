//! Redaction utilities for cloud-mode logging/payloads.
//!
//! Cloud mode must never log or report secrets. Git and HTTP error strings can
//! contain embedded credentials (for example, URLs with `user:pass@host`).
//!
//! This module provides a conservative sanitizer for untrusted error strings.

use itertools::Itertools;

/// Redact likely secrets from an untrusted, user-controlled string.
///
/// This is intentionally conservative. It may redact non-secret strings if they
/// resemble tokens.
#[must_use]
pub fn redact_secrets(input: &str) -> String {
    truncate_redacted(&redact_token_like_substrings(&redact_bearer_tokens(
        &redact_common_query_params(&redact_http_url_userinfo(input)),
    )))
}

fn truncate_redacted(input: &str) -> String {
    const MAX_LEN: usize = 4096;

    if input.len() <= MAX_LEN {
        return input.to_string();
    }

    format!("{}...<truncated>", &input[..MAX_LEN])
}

fn redact_http_url_userinfo(input: &str) -> String {
    let http_positions: Vec<(usize, &str)> = [("https://", "https://"), ("http://", "http://")]
        .iter()
        .flat_map(|(pattern, replacement)| {
            input
                .match_indices(*pattern)
                .map(move |(idx, _)| (idx, *replacement))
        })
        .collect();

    if http_positions.is_empty() {
        return input.to_string();
    }

    let sorted_positions: Vec<(usize, &str)> = http_positions
        .into_iter()
        .sorted_by_key(|(idx, _)| *idx)
        .collect();

    let (result_parts, last_end): (Vec<&str>, usize) = sorted_positions.iter().fold(
        (Vec::new(), 0usize),
        |(parts, last_end): (Vec<&str>, usize), (start, scheme): &(usize, &str)| {
            let new_parts: Vec<&str> = if *start > last_end {
                parts
                    .iter()
                    .copied()
                    .chain(std::iter::once(&input[last_end..*start]))
                    .collect()
            } else {
                parts
            };

            let scheme_len = scheme.len();
            let authority_start = start + scheme_len;
            let authority_end = input[authority_start..]
                .find(|c: char| c == '/' || c.is_ascii_whitespace())
                .map(|pos| authority_start + pos)
                .unwrap_or(input.len());

            let authority = &input[authority_start..authority_end];
            let final_parts: Vec<&str> = if let Some(at_pos) = authority.rfind('@') {
                new_parts
                    .iter()
                    .copied()
                    .chain(std::iter::once(*scheme))
                    .chain(std::iter::once("<redacted>@"))
                    .chain(std::iter::once(&authority[at_pos + 1..]))
                    .collect()
            } else {
                new_parts
                    .iter()
                    .copied()
                    .chain(std::iter::once(*scheme))
                    .chain(std::iter::once(authority))
                    .collect()
            };

            (final_parts, authority_end)
        },
    );

    if last_end < input.len() {
        result_parts
            .iter()
            .copied()
            .chain(std::iter::once(&input[last_end..]))
            .collect::<Vec<_>>()
            .concat()
    } else {
        result_parts.concat()
    }
}

fn redact_bearer_tokens(input: &str) -> String {
    // Replace `Bearer <token>` with `Bearer <redacted>` (case-insensitive match on "bearer").
    // Use regex for case-insensitive matching

    // Pattern matches "bearer " (case-insensitive) followed by non-whitespace characters (the token)
    let pattern = regex::Regex::new(r"(?i)(bearer\s+)\S+").expect("valid regex");

    pattern.replace_all(input, "$1<redacted>").to_string()
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

    // Build regex pattern from keys
    let pattern = format!("(?i)({})([^&\\s]*)", KEYS.join("|"));
    let re = regex::Regex::new(&pattern).expect("valid regex");

    re.replace_all(input, |caps: &regex::Captures| {
        let key = caps.get(1).map_or("", |m| m.as_str());
        format!("{}<redacted>", key)
    })
    .to_string()
}

fn redact_token_like_substrings(input: &str) -> String {
    // Redact substrings that look like common tokens, even if not in a URL.
    // Examples: GitHub PATs, GitLab PATs, Slack tokens, Google OAuth tokens.

    const PREFIXES: [&str; 6] = ["ghp_", "github_pat_", "glpat-", "xoxb-", "xapp-", "ya29."];

    // Build regex pattern from prefixes - match prefix followed by token chars
    let pattern = format!(
        "({})[A-Za-z0-9_\\-\\.]+",
        PREFIXES
            .iter()
            .map(|p| regex::escape(p))
            .collect::<Vec<_>>()
            .join("|")
    );
    let re = regex::Regex::new(&pattern).expect("valid regex");

    re.replace_all(input, "<redacted>").to_string()
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
