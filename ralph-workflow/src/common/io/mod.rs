//! Boundary module for common I/O and preprocessing utilities.
//!
//! This module is exempt from functional programming lints because it contains
//! lazy-initialized statics and other imperative patterns required for
//! performance and correctness.

use std::sync::LazyLock;

static SECRET_LIKE_RE: LazyLock<Option<regex::Regex>> = LazyLock::new(|| {
    regex::Regex::new(
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
        )
\b
        ",
    )
    .ok()
});

#[must_use]
pub fn secret_like_regex() -> Option<regex::Regex> {
    SECRET_LIKE_RE.clone()
}

/// Redact a value that may contain secrets using the secret-like regex.
///
/// If the key is sensitive, returns "<redacted>". Otherwise, replaces any
/// secret-like patterns in the value with "<redacted>".
pub fn redact_arg_value(key: &str, value: &str) -> String {
    if is_sensitive_key(key) {
        return "<redacted>".to_string();
    }
    secret_like_regex().map_or_else(
        || value.to_string(),
        |re| re.replace_all(value, "<redacted>").to_string(),
    )
}

/// Check if a key looks like a sensitive configuration key.
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

/// Shell-quote a string for safe logging.
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
///
/// This is the boundary function that calls into the pure is_sensitive_key
/// and uses the secret_like_regex for pattern-based redaction.
pub fn format_argv_for_log(argv: &[String]) -> String {
    let indices = 0..argv.len();
    let out: Vec<String> = indices
        .map(|i| {
            let arg = &argv[i];
            let prev_was_sensitive = i > 0 && is_sensitive_key(&argv[i - 1]);

            if prev_was_sensitive {
                return "<redacted>".to_string();
            }

            if let Some((k, v)) = arg.split_once('=') {
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
