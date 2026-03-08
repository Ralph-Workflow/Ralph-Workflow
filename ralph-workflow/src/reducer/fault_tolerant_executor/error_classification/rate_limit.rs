//! Rate limit error detection for agent error classification.
//!
//! This module provides functions to detect rate limit and usage limit errors
//! from agent output (both stderr and stdout/logfile sources).

use serde_json::Value;

/// Check for rate limit errors from both stderr and stdout sources.
///
/// This function examines:
/// 1. stderr (traditional error output)
/// 2. `stdout_error` (extracted from JSON logs, e.g., `OpenCode`)
///
/// This dual-source approach ensures rate limit errors are detected
/// regardless of which stream the agent uses for error reporting.
pub(super) fn is_rate_limit_error_from_any_source(
    stderr_lower: &str,
    stderr_raw: &str,
    stdout_error: Option<&str>,
) -> bool {
    // Check stderr first (traditional path)
    if is_rate_limit_stderr(stderr_lower, stderr_raw) {
        return true;
    }

    // Check stdout error message if available (e.g., from OpenCode JSON logs)
    if let Some(stdout_err) = stdout_error {
        let stdout_lower = stdout_err.to_lowercase();
        if is_rate_limit_stderr(&stdout_lower, stdout_err) {
            return true;
        }
    }

    false
}

fn is_rate_limit_stderr(stderr_lower: &str, stderr_raw: &str) -> bool {
    // PRIORITY 1: Check if input is a direct error code (from JSON extraction)
    // When extract_error_identifier_from_logfile extracts error codes from OpenCode JSON,
    // it returns the bare code string (e.g., "usage_limit_exceeded", not the full JSON).
    // We must check for these codes directly before trying to parse JSON.
    if is_direct_error_code(stderr_raw) {
        return true;
    }

    // PRIORITY 2: Prefer structured JSON formats when available.
    if is_structured_rate_limit_error(stderr_raw) {
        return true;
    }

    // Match documented OpenAI 429 wording (avoid broad substring matches like "429" or "quota").
    if stderr_lower.contains("rate limit reached") || stderr_lower.contains("rate limit exceeded") {
        return true;
    }

    if stderr_lower.contains("too many requests") {
        return true;
    }

    // Providers sometimes emit a bare status indication (e.g., "HTTP 429") without additional
    // phrases; treat any clear HTTP/status 429 marker as RateLimit.
    if stderr_lower.contains("http 429") || stderr_lower.contains("status 429") {
        return true;
    }

    // Anthropic Claude API patterns - HTTP 529 overloaded_error (server overload)
    // Distinct from HTTP 429 rate limiting: 529 indicates temporary server capacity issues
    // that should trigger immediate agent fallback rather than retry with the same agent.
    if stderr_lower.contains("http 529")
        || stderr_lower.contains("status 529")
        || (stderr_lower.contains("overloaded")
            && (stderr_lower.contains("api") || stderr_lower.contains("server")))
    {
        return true;
    }

    // Quota exhaustion patterns - align with agents/error.rs
    //
    // Source: /packages/opencode/src/provider/error.ts
    // OpenCode emits "Quota exceeded. Check your plan and billing details."
    // for insufficient_quota errors (verified 2026-02-12)
    if stderr_lower.contains("exceeded your current quota")
        || stderr_lower.contains("quota exceeded")
        || stderr_lower.contains("insufficient_quota")
    {
        return true;
    }

    // Usage limit patterns (observed from OpenCode/multi-provider gateways)
    //
    // Bug Fix Context: OpenCode and similar multi-provider gateways emit
    // "usage limit has been reached [retryin]" when underlying providers
    // (OpenAI, Anthropic, etc.) hit quota/usage limits.
    //
    // The "[retryin]" suffix is misleading - the agent is actually unavailable
    // due to quota exhaustion and should trigger immediate agent fallback, not retry.
    //
    // Detection: Match multiple patterns:
    // 1. "usage limit has been reached" - Full phrase with timeout suffix
    // 2. "usage limit reached" - Shorter variant
    // 3. "usage limit exceeded" - Alternative wording variant
    // 4. OpenCode Zen/OpenCode-specific patterns with provider context
    // 5. Multi-provider gateway forwarding pattern (e.g., "anthropic: usage limit reached")
    // 6. Bare "usage limit" - With API error context to avoid false positives
    //
    // For the bare "usage limit" pattern, we require API error context to avoid
    // false positives from filenames (e.g., "usage_limit.rs") or non-error text.
    // Context markers: "error:" prefix, sentence punctuation, or HTTP status codes.
    //
    // Last Verified: 2026-02-12
    // Source: OpenCode production logs and multi-provider gateway behavior
    // How to verify:
    //   1. Check OpenCode source at https://github.com/anomalyco/opencode
    //   2. Review /packages/opencode/src/cli/cmd/run.ts for error emission
    //   3. Test with OpenCode CLI near usage limit to observe actual messages
    //   4. Update patterns if format changes
    //
    // Providers affected: OpenCode (multi-provider), Claude API wrappers
    // Related patterns: "quota exceeded", "rate limit exceeded"
    if stderr_lower.contains("usage limit has been reached")
        || stderr_lower.contains("usage limit reached")
    {
        return true;
    }

    // OpenCode alternative wording: "usage limit exceeded"
    // Some providers use "exceeded" instead of "reached"
    if stderr_lower.contains("usage limit exceeded") {
        return true;
    }

    // OpenCode Zen/OpenCode-specific patterns with provider context
    // Pattern: "zen usage limit" or "opencode usage limit"
    // These indicate usage limit errors from OpenCode Zen or OpenCode gateway
    if (stderr_lower.contains("zen") || stderr_lower.contains("opencode"))
        && stderr_lower.contains("usage limit")
    {
        return true;
    }

    // Multi-provider gateway forwarding pattern
    // Pattern: "<provider>: usage limit" (e.g., "anthropic: usage limit reached")
    // OpenCode multi-provider gateway forwards errors from underlying providers
    // with a provider prefix to distinguish error sources.
    //
    // IMPORTANT: This check must exclude filename patterns to avoid false positives.
    // The pattern "error: usage limit.rs file not found" should NOT match because
    // "usage limit" is followed by a file extension, not additional error context.
    if contains_provider_prefixed_usage_limit(stderr_lower) {
        return true;
    }

    // Bare "usage limit" pattern with context requirements
    // Match only when in API error context to avoid false positives
    if stderr_lower.contains("usage limit") {
        // Exclude cases where every occurrence is actually a filename (e.g., "usage limit.rs").
        // IMPORTANT: do not return false just because *some* occurrences look like filenames;
        // stderr can contain both a filename diagnostic and a later provider error.
        let has_non_filename_usage_limit = has_non_filename_occurrence(stderr_lower, "usage limit")
            || has_non_filename_occurrence(stderr_lower, "usage_limit");

        if !has_non_filename_usage_limit {
            return false;
        }

        // Check for API error context markers:
        // - Preceded by "error:" or similar error indicators
        // - Followed by sentence-ending punctuation (., !, ;) but NOT file extension
        // - Preceded by HTTP status markers (already partially covered above)
        let has_error_prefix = stderr_lower.contains("error: usage limit")
            || stderr_lower.contains("usage limit.")
            || stderr_lower.contains("usage limit!")
            || stderr_lower.contains("usage limit;")
            || stderr_lower.contains("usage limit,")
            || (stderr_lower.contains("http 429") && stderr_lower.contains("usage limit"))
            || (stderr_lower.contains("status 429") && stderr_lower.contains("usage limit"));

        if has_error_prefix {
            return true;
        }
    }

    // Google Gemini API patterns - RESOURCE_EXHAUSTED status (HTTP 429)
    if stderr_lower.contains("resource_exhausted") {
        return true;
    }

    false
}

/// Check if input is a direct error code (not wrapped in JSON).
///
/// When `extract_error_identifier_from_logfile` extracts error codes from `OpenCode` JSON logfiles,
/// it returns the bare error code string (e.g., `"usage_limit_exceeded"`), not the full JSON.
///
/// This function detects these bare error codes so they are properly classified as `RateLimit`.
///
/// Supported codes:
/// - `usage_limit_exceeded`: Usage/quota limit reached
/// - `quota_exceeded`: Quota exhausted
/// - `usage_limit_reached`: Alternative usage limit code
/// - `insufficient_quota`: `OpenAI` quota exhaustion
/// - `rate_limit_exceeded`: Standard rate limit error
fn is_direct_error_code(text: &str) -> bool {
    // Trim whitespace to handle cases where the code might have surrounding whitespace
    let trimmed = text.trim();

    // Check if the entire string is exactly one of the known error codes
    // This avoids false positives from JSON or other text containing these codes
    matches!(
        trimmed,
        "usage_limit_exceeded"
            | "quota_exceeded"
            | "usage_limit_reached"
            | "insufficient_quota"
            | "rate_limit_exceeded"
    )
}

fn is_structured_rate_limit_error(stderr: &str) -> bool {
    // OpenCode (and some providers) emit structured JSON errors containing a stable code.
    // Example observed in CI:
    //   "✗ Error: {\"type\":\"error\",...,\"error\":{\"code\":\"rate_limit_exceeded\",...}}"
    //
    // Supported error codes:
    // - rate_limit_exceeded: Standard rate limit error (HTTP 429)
    // - usage_limit_exceeded: Usage/quota limit reached (OpenCode-specific)
    // - quota_exceeded: Quota exhausted (provider-specific)
    // - usage_limit_reached: Alternative usage limit code (OpenCode-specific)
    //
    // Error codes are more stable than message text and should be preferred
    // for detection when available.
    let Some(value) = try_parse_json_object(stderr) else {
        return false;
    };

    let code = extract_error_code(&value);

    // Standard rate limit code
    if matches!(code.as_deref(), Some("rate_limit_exceeded")) {
        return true;
    }

    // OpenCode-specific usage limit error codes
    // These indicate quota/usage exhaustion from underlying providers
    //
    // Source: /packages/opencode/src/provider/error.ts in OpenCode repository
    // - insufficient_quota: OpenAI quota exhaustion (verified 2026-02-12)
    // - usage_limit_exceeded: Generic usage limit (OpenCode gateway)
    // - quota_exceeded: Provider quota exhaustion
    // - usage_limit_reached: Alternative usage limit code
    if matches!(
        code.as_deref(),
        Some(
            "usage_limit_exceeded"
                | "quota_exceeded"
                | "usage_limit_reached"
                | "insufficient_quota"
        )
    ) {
        return true;
    }

    false
}

fn try_parse_json_object(text: &str) -> Option<Value> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    let json_str = text.get(start..=end)?;
    serde_json::from_str(json_str).ok()
}

fn extract_error_code(value: &Value) -> Option<String> {
    // Support a couple of common nestings.
    // - OpenCode: {"error": {"code": "rate_limit_exceeded", ...}}
    // - Some SDKs: {"error": {"error": {"code": "..."}}}
    value
        .pointer("/error/code")
        .and_then(Value::as_str)
        .map(std::string::ToString::to_string)
        .or_else(|| {
            value
                .pointer("/error/error/code")
                .and_then(Value::as_str)
                .map(std::string::ToString::to_string)
        })
}

/// Check if a pattern is followed by a file extension (with optional whitespace).
///
/// This prevents false positives where "usage limit" appears as part of a filename
/// (e.g., "error: usage limit.rs file not found" or "error: usage limit .rs file not found")
/// rather than as an API error message.
///
/// Uses a regex pattern to match any file extension (dot followed by 1-5 alphanumeric chars).
/// This covers all common programming language file extensions and is future-proof.
///
/// The regex is compiled once at startup using `LazyLock` for efficiency.
///
/// # Arguments
/// * `text` - The full text to search in (lowercase)
/// * `pattern` - The pattern to check (e.g., "usage limit", "`usage_limit`")
///
/// # Returns
/// `true` if the pattern is found and is followed by a file extension pattern
fn contains_provider_prefixed_usage_limit(text_lower: &str) -> bool {
    // Multi-provider gateway forwarding pattern.
    // Pattern: "<provider>: usage limit" (e.g., "anthropic: usage limit")
    // IMPORTANT: Some stderr contains multiple occurrences; we must inspect each matching occurrence
    // rather than only the first "usage limit" substring in the text.
    for (pos, _) in text_lower.match_indices(": usage limit") {
        let usage_limit_pos = pos + ": ".len();
        if !is_followed_by_file_extension_generic_at(text_lower, usage_limit_pos, "usage limit") {
            return true;
        }
    }

    false
}

fn has_non_filename_occurrence(text_lower: &str, pattern: &str) -> bool {
    for (pos, _) in text_lower.match_indices(pattern) {
        if !is_followed_by_file_extension_generic_at(text_lower, pos, pattern) {
            return true;
        }
    }

    false
}

fn is_followed_by_file_extension_generic_at(
    text_lower: &str,
    pattern_pos: usize,
    pattern: &str,
) -> bool {
    /// `LazyLock` for one-time regex initialization (compiled on first use, then cached).
    /// Pattern matches: optional whitespace + dot + 1-10 alphanumeric chars + non-alphanumeric or end.
    /// Matches common file extensions: .rs, .py, .js, .ts, .go, .rb, .java, .cpp, .c, .h, .php, .cs, .swift, .kt, .scala, .sh, .properties, .markdown, .terraform, etc.
    /// Handles edge case of whitespace between pattern and extension: "usage limit .rs"
    /// Updated from 1-5 to 1-10 to support longer extensions like .properties, .markdown
    static EXTENSION_REGEX: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"^\s*\.[a-z0-9]{1,10}([^a-z0-9]|$)").unwrap()
    });

    let Some(after_pattern) = text_lower.get(pattern_pos + pattern.len()..) else {
        return false;
    };

    if after_pattern.is_empty() {
        return false;
    }

    EXTENSION_REGEX.is_match(after_pattern)
}
