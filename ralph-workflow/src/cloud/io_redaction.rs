use std::sync::LazyLock;

static BEARER_TOKEN_RE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"(?i)(bearer\s+)\S+").expect("valid regex"));

static COMMON_QUERY_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
    const KEYS: [&str; 5] = [
        "access_token=",
        "token=",
        "password=",
        "passwd=",
        "oauth_token=",
    ];
    let pattern = format!("(?i)({})([^&\\s]*)", KEYS.join("|"));
    regex::Regex::new(&pattern).expect("valid regex")
});

static TOKEN_LIKE_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
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
});

pub fn redact_bearer_tokens(input: &str) -> String {
    BEARER_TOKEN_RE
        .replace_all(input, "$1<redacted>")
        .to_string()
}

pub fn redact_common_query_params(input: &str) -> String {
    COMMON_QUERY_RE
        .replace_all(input, |caps: &regex::Captures| {
            let key = caps.get(1).map_or("", |m| m.as_str());
            format!("{}<redacted>", key)
        })
        .to_string()
}

pub fn redact_token_like_substrings(input: &str) -> String {
    TOKEN_LIKE_RE.replace_all(input, "<redacted>").to_string()
}
