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