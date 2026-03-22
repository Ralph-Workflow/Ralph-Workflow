fn bearer_token_re() -> regex::Regex {
    regex::Regex::new(r"(?i)(bearer\s+)\S+").expect("valid regex")
}

fn common_query_re() -> regex::Regex {
    const KEYS: [&str; 5] = [
        "access_token=",
        "token=",
        "password=",
        "passwd=",
        "oauth_token=",
    ];
    let pattern = format!("(?i)({})([^&\\s]*)", KEYS.join("|"));
    regex::Regex::new(&pattern).expect("valid regex")
}

fn token_like_re() -> regex::Regex {
    const PREFIXES: [&str; 6] = ["ghp_", "github_pat_", "glpat-", "xoxb-", "xapp-", "ya29."];
    let pattern = format!(
        "({})[A-Za-z0-9_\\-\\.]+",
        PREFIXES
            .iter()
            .map(|&s| regex::escape(s))
            .collect::<Vec<_>>()
            .join("|")
    );
    regex::Regex::new(&pattern).expect("valid regex")
}

pub fn redact_bearer_tokens(input: &str) -> String {
    bearer_token_re()
        .replace_all(input, "$1<redacted>")
        .to_string()
}

pub fn redact_common_query_params(input: &str) -> String {
    common_query_re()
        .replace_all(input, |caps: &regex::Captures| {
            let key = caps.get(1).map_or("", |m| m.as_str());
            format!("{}<redacted>", key)
        })
        .to_string()
}

pub fn redact_token_like_substrings(input: &str) -> String {
    token_like_re().replace_all(input, "<redacted>").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- redact_bearer_tokens ---

    #[test]
    fn redact_bearer_tokens_replaces_token_value() {
        let input = "Authorization: Bearer abc123xyz";
        let result = redact_bearer_tokens(input);
        assert!(result.contains("Bearer <redacted>"), "got: {result}");
        assert!(!result.contains("abc123xyz"), "token should be redacted");
    }

    #[test]
    fn redact_bearer_tokens_case_insensitive() {
        let input = "authorization: bearer SECRET_TOKEN";
        let result = redact_bearer_tokens(input);
        assert!(result.contains("<redacted>"), "got: {result}");
        assert!(!result.contains("SECRET_TOKEN"), "token should be redacted");
    }

    #[test]
    fn redact_bearer_tokens_no_match_unchanged() {
        let input = "Hello, no tokens here.";
        assert_eq!(redact_bearer_tokens(input), input);
    }

    // --- redact_common_query_params ---

    #[test]
    fn redact_common_query_params_replaces_access_token() {
        let input = "https://example.com/api?access_token=supersecret&foo=bar";
        let result = redact_common_query_params(input);
        assert!(result.contains("access_token=<redacted>"), "got: {result}");
        assert!(!result.contains("supersecret"), "value should be redacted");
        assert!(
            result.contains("foo=bar"),
            "unrelated param should be untouched"
        );
    }

    #[test]
    fn redact_common_query_params_replaces_password() {
        let input = "https://example.com/api?password=hunter2";
        let result = redact_common_query_params(input);
        assert!(result.contains("password=<redacted>"), "got: {result}");
        assert!(!result.contains("hunter2"), "password should be redacted");
    }

    #[test]
    fn redact_common_query_params_no_match_unchanged() {
        let input = "https://example.com/api?foo=bar&baz=qux";
        assert_eq!(redact_common_query_params(input), input);
    }

    // --- redact_token_like_substrings ---

    #[test]
    fn redact_token_like_substrings_replaces_github_pat() {
        let input = "token=ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345";
        let result = redact_token_like_substrings(input);
        assert!(result.contains("<redacted>"), "got: {result}");
        assert!(
            !result.contains("ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345"),
            "PAT should be redacted"
        );
    }

    #[test]
    fn redact_token_like_substrings_replaces_gitlab_token() {
        let input = "CI_TOKEN=glpat-xyz_abc123";
        let result = redact_token_like_substrings(input);
        assert!(result.contains("<redacted>"), "got: {result}");
        assert!(
            !result.contains("glpat-xyz_abc123"),
            "GitLab PAT should be redacted"
        );
    }

    #[test]
    fn redact_token_like_substrings_no_match_unchanged() {
        let input = "nothing sensitive here";
        assert_eq!(redact_token_like_substrings(input), input);
    }
}
