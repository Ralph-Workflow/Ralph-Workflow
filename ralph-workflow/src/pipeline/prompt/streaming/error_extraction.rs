use std::path::Path;

/// Extract `session_id` from a log file.
pub fn extract_session_id_from_logfile(
    logfile: &str,
    workspace: &dyn crate::workspace::Workspace,
) -> Option<String> {
    let logfile_path = Path::new(logfile);
    let content = workspace.read(logfile_path).ok()?;

    content
        .lines()
        .take(10)
        .find_map(extract_session_id_from_json_line)
}

/// Extract `session_id` from a single JSON line.
///
/// Supports multiple agent formats:
/// - Claude: `{"type":"system","subtype":"init","session_id":"abc123"}`
/// - Gemini: `{"type":"init","session_id":"abc123","model":"gemini-pro"}`
/// - `OpenCode`: `{"event_type":"...", "session_id":"abc123"}`
pub(super) fn extract_session_id_from_json_line(line: &str) -> Option<String> {
    // Try to parse as JSON
    let value: serde_json::Value = serde_json::from_str(line).ok()?;

    // Check for session_id field (common across formats)
    if let Some(session_id) = value.get("session_id").and_then(|v| v.as_str()) {
        return Some(session_id.to_string());
    }

    // Check for sessionID field (some agents use camelCase)
    if let Some(session_id) = value.get("sessionID").and_then(|v| v.as_str()) {
        return Some(session_id.to_string());
    }

    None
}

/// Extract a human-readable error message from a logfile containing agent JSON output.
///
/// This function searches for error events in the logfile (typically from stdout)
/// and extracts the error message. This is critical for agents like `OpenCode` that
/// emit errors as JSON to stdout rather than stderr.
///
/// Supported error formats:
/// - `OpenCode`: `{"type":"error","error":{"message":"usage limit reached"}}`
/// - `OpenCode`: `{"type":"error","error":{"data":{"message":"Invalid API key"}}}`
/// - Claude: `{"type":"error","message":"Rate limit exceeded"}`
///
/// Note: For safety, this extractor only considers lines explicitly marked as
/// error events (`{"type":"error", ...}`). It will ignore JSON that merely
/// contains an `error` object but is not tagged as an error event.
///
/// # Arguments
///
/// * `logfile` - Path to the agent's log file
/// * `workspace` - Workspace for file access
///
/// # Returns
///
/// The extracted error message, or `None` if no error found
pub fn extract_error_message_from_logfile(
    logfile: &str,
    workspace: &dyn crate::workspace::Workspace,
) -> Option<String> {
    let logfile_path = Path::new(logfile);
    let content = workspace.read(logfile_path).ok()?;

    content
        .lines()
        .rev()
        .take(50)
        .find_map(extract_error_message_from_json_line)
}

/// Extract an error identifier from a logfile containing agent JSON output.
///
/// This is intended for programmatic classification (rate limit, quota, auth),
/// and prefers stable error codes when available.
pub fn extract_error_identifier_from_logfile(
    logfile: &str,
    workspace: &dyn crate::workspace::Workspace,
) -> Option<String> {
    let logfile_path = Path::new(logfile);
    let content = workspace.read(logfile_path).ok()?;

    content
        .lines()
        .rev()
        .take(50)
        .find_map(extract_error_identifier_from_json_line)
}

pub(super) fn is_explicit_error_event(value: &serde_json::Value) -> bool {
    matches!(value.get("type").and_then(|v| v.as_str()), Some("error"))
}

pub(super) fn error_code_to_human_message(code: &str) -> Option<&'static str> {
    // Keep these short and user-facing; callers that need stable identifiers
    // should use `extract_error_identifier_from_logfile`.
    match code {
        "usage_limit_exceeded" | "usage_limit_reached" => Some("usage limit reached"),
        "rate_limit_exceeded" => Some("rate limit exceeded"),
        "quota_exceeded" | "insufficient_quota" => Some("quota exceeded"),
        _ => None,
    }
}

/// Extract a human-readable error message from a single JSON line.
///
/// Supports multiple agent error formats:
/// - `OpenCode`: `{"type":"error","error":{"message":"..."}}`
/// - `OpenCode`: `{"type":"error","error":{"data":{"message":"..."}}}`
/// - `OpenCode`: `{"type":"error","error":{"name":"APIError"}}`
/// - `OpenCode`: `{"type":"error","error":{"code":"usage_limit_exceeded"}}`
/// - `OpenCode`: `{"type":"error","error":{"provider":"anthropic","message":"..."}}`
/// - Claude: `{"type":"error","message":"..."}`
///
/// This extractor requires an explicit error marker (`type == "error"`) to avoid
/// false positives from non-error events that include an `error` object.
///
/// # `OpenCode` Error Code Detection
///
/// `OpenCode` (and some providers) emit structured JSON errors with stable error codes.
/// Error codes are more reliable than message text for detection because they don't
/// change across `OpenCode` versions or provider updates.
///
/// Supported error codes (verified 2026-02-12 against `OpenCode` source):
/// - `usage_limit_exceeded`: Usage/quota limit reached
/// - `rate_limit_exceeded`: Rate limit reached
/// - `quota_exceeded`: Quota exhausted
/// - `insufficient_quota`: `OpenAI` quota exhaustion (source: /packages/opencode/src/provider/error.ts)
/// - `usage_limit_reached`: Alternative usage limit code
///
/// Source: <https://github.com/anomalyco/opencode>
/// - /packages/opencode/src/cli/cmd/run.ts (error emission)
/// - /packages/opencode/src/session/message-v2.ts (error format definitions)
/// - /packages/opencode/src/provider/error.ts (error code parsing)
///
/// # Provider-Specific Error Format
///
/// `OpenCode` multi-provider gateway forwards errors from underlying providers
/// (`OpenAI`, Anthropic, Google, etc.) with a `provider` field:
///
/// ```json
/// {
///   "type": "error",
///   "error": {
///     "provider": "anthropic",
///     "message": "usage limit reached"
///   }
/// }
/// ```
///
/// This format captures provider-specific usage limit errors that should trigger
/// agent fallback.
pub(super) fn extract_error_message_from_json_line(line: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if !is_explicit_error_event(&value) {
        return None;
    }

    // Prefer human-readable messages over codes.
    if let Some(provider) = value.pointer("/error/provider").and_then(|v| v.as_str()) {
        if let Some(msg) = value.pointer("/error/message").and_then(|v| v.as_str()) {
            return Some(format!("{provider}: {msg}"));
        }
    }

    if let Some(data_message) = value
        .pointer("/error/data/message")
        .and_then(|v| v.as_str())
    {
        return Some(data_message.to_string());
    }

    if let Some(error_message) = value.pointer("/error/message").and_then(|v| v.as_str()) {
        return Some(error_message.to_string());
    }

    // Claude format: {"type":"error","message":"..."}
    if let Some(message) = value.get("message").and_then(|v| v.as_str()) {
        return Some(message.to_string());
    }

    // If we only have a code, map it to a short human message when possible.
    if let Some(code) = value.pointer("/error/code").and_then(|v| v.as_str()) {
        if let Some(mapped) = error_code_to_human_message(code) {
            return Some(mapped.to_string());
        }
    }

    // Fallback: error name, if present.
    value
        .pointer("/error/name")
        .and_then(|v| v.as_str())
        .map(ToString::to_string)
}

pub(super) fn extract_error_identifier_from_json_line(line: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if !is_explicit_error_event(&value) {
        return None;
    }

    // Prefer stable error codes when available.
    if let Some(code) = value.pointer("/error/code").and_then(|v| v.as_str()) {
        return Some(code.to_string());
    }

    // Next best: provider-qualified message.
    if let Some(provider) = value.pointer("/error/provider").and_then(|v| v.as_str()) {
        if let Some(msg) = value.pointer("/error/message").and_then(|v| v.as_str()) {
            return Some(format!("{provider}: {msg}"));
        }
    }

    // Then: plain message forms.
    if let Some(data_message) = value
        .pointer("/error/data/message")
        .and_then(|v| v.as_str())
    {
        return Some(data_message.to_string());
    }
    if let Some(error_message) = value.pointer("/error/message").and_then(|v| v.as_str()) {
        return Some(error_message.to_string());
    }
    if let Some(message) = value.get("message").and_then(|v| v.as_str()) {
        return Some(message.to_string());
    }

    // Last: error name.
    value
        .pointer("/error/name")
        .and_then(|v| v.as_str())
        .map(ToString::to_string)
}
