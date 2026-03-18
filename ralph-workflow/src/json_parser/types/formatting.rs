use crate::common::truncate_text;
use crate::json_parser::stream_classifier::{
    ClassificationResult, StreamEventClassifier, StreamEventType,
};
use regex::Regex;

static SECRET_VALUE_RE: std::sync::LazyLock<Option<Regex>> = std::sync::LazyLock::new(|| {
    // Keep this intentionally conservative to reduce false positives in normal text.
    // Primary goal: avoid leaking common API key formats to stdout/logs.
    Regex::new(
        r"(?xi)
        \bsk-[a-z0-9]{16,}\b          # OpenAI-style keys
        | \bghp_[a-z0-9]{20,}\b       # GitHub PATs
        | \bxox[baprs]-[a-z0-9-]{10,}\b # Slack tokens
        ",
    )
    .ok()
});

fn is_sensitive_key(key: &str) -> bool {
    let normalized = key
        .to_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect::<String>();

    // Common sensitive key patterns. We intentionally use `contains` to catch variants like:
    // access_token, apiKey, openai_api_key, githubToken, bearerToken, etc.
    normalized.contains("token")
        || normalized.contains("apikey")
        || normalized.contains("secret")
        || normalized.contains("password")
        || normalized == "authorization"
        || normalized.contains("bearer")
}

fn looks_like_secret_value(value: &str) -> bool {
    SECRET_VALUE_RE
        .as_ref()
        .is_some_and(|re| re.is_match(value))
}

fn format_tool_value(key: Option<&str>, value: &serde_json::Value) -> String {
    if let Some(k) = key {
        if is_sensitive_key(k) {
            return "<redacted>".to_string();
        }
    }

    match value {
        serde_json::Value::String(s) => {
            if looks_like_secret_value(s) {
                "<redacted>".to_string()
            } else {
                // Use character-safe truncation for strings
                truncate_text(s, 100)
            }
        }
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::Bool(b) => b.to_string(),
        serde_json::Value::Null => "null".to_string(),
        serde_json::Value::Array(arr) => format!("[{} items]", arr.len()),
        serde_json::Value::Object(_) => "{...}".to_string(),
    }
}

/// Format tool input for display.
///
/// Converts JSON input to a human-readable string, showing key parameters.
/// Uses character-safe truncation to handle UTF-8 properly.
#[must_use]
pub fn format_tool_input(input: &serde_json::Value) -> String {
    match input {
        serde_json::Value::Object(map) => {
            let parts: Vec<String> = map
                .iter()
                .map(|(k, v)| {
                    let val_str = format_tool_value(Some(k.as_str()), v);
                    format!("{k}={val_str}")
                })
                .collect();
            parts.join(", ")
        }
        serde_json::Value::String(_) => format_tool_value(None, input),
        other => other.to_string(),
    }
}

/// Helper function to extract text from nested JSON structures.
///
/// This function attempts to extract text content from common nested patterns
/// like `{"text": "..."}`, `{"delta": {"text": "..."}}`, etc.
fn extract_nested_text(value: &serde_json::Value) -> Option<String> {
    // If it's already a string, return it
    if let Some(text) = value.as_str() {
        return Some(text.to_string());
    }

    // If it's an object, look for common text fields
    if let Some(obj) = value.as_object() {
        // Check common text field names
        return ["text", "content", "message", "output", "result"]
            .iter()
            .find_map(|field| {
                obj.get(*field)
                    .and_then(|val| val.as_str())
                    .map(|s| s.to_string())
            });
    }

    None
}

/// Extract the event type and classification from a JSON object.
///
/// Returns the event type string and the classification result.
fn extract_event_type_and_classify(
    obj: &serde_json::Map<String, serde_json::Value>,
    value: &serde_json::Value,
) -> (String, StreamEventType) {
    // Use stream classifier for algorithmic event detection
    let classifier = StreamEventClassifier::new();
    let classification = classifier.classify(value);

    // Extract the type field - try both "type" and common variants
    let event_type = obj
        .get("type")
        .and_then(|v| v.as_str())
        .or_else(|| obj.get("event_type").and_then(|v| v.as_str()))
        .unwrap_or_else(|| {
            // Use classifier's detected type name if available
            classification.type_name.as_deref().unwrap_or("unknown")
        });

    (event_type.to_string(), classification.event_type)
}

/// Extract partial/delta content from a JSON object for verbose display.
///
/// Returns truncated content info string or None.
fn extract_partial_content(
    obj: &serde_json::Map<String, serde_json::Value>,
    classification: &ClassificationResult,
) -> Option<String> {
    // Try to extract content from various nested structures
    let extracted_text = classification
        .content_field
        .as_ref()
        .and_then(|content| {
            // Content field was found at top level by classifier
            obj.get(content).and_then(|val| {
                val.as_str().map_or_else(
                    || {
                        // Content field exists but is not a string - try to extract nested text
                        extract_nested_text(val)
                    },
                    |s| Some(s.to_string()),
                )
            })
        })
        .or_else(|| {
            // No content field found - try to extract from delta field
            obj.get("delta")
                .and_then(extract_nested_text)
                .or_else(|| {
                    // Try nested delta structure: delta.text or delta.content
                    obj.get("delta")
                        .and_then(|d| d.as_object())
                        .and_then(|delta_obj| {
                            // First try delta.text, then delta.content
                            delta_obj
                                .get("text")
                                .or_else(|| delta_obj.get("content"))
                                .and_then(|v| v.as_str())
                                .map(std::string::ToString::to_string)
                        })
                })
                .or_else(|| {
                    // Try other common nested structures
                    obj.get("data").and_then(extract_nested_text)
                })
        });

    extracted_text.map(|text: String| {
        let truncated = if text.chars().count() > 30 {
            // Use character-based slicing to avoid panic on multi-byte UTF-8 characters
            let chars: Vec<char> = text.chars().take(27).collect();
            format!("{}...", chars.iter().collect::<String>())
        } else {
            text
        };
        format!(" content=\"{truncated}\"")
    })
}

/// Build the type label for an unknown event based on classification.
///
/// Returns formatted label or early-returns `String::new()` for suppression.
fn build_type_label(
    event_type: &str,
    obj: &serde_json::Map<String, serde_json::Value>,
    classification: &ClassificationResult,
    is_verbose: bool,
) -> String {
    match classification.event_type {
        StreamEventType::Partial => {
            // Show partial events if they have explicit delta indicators in type name OR
            // if they have an actual delta field (not just algorithmically detected).
            // Note: The presence of a delta/partial/chunk field (regardless of whether it's
            // a boolean flag or string content) indicates streaming protocol behavior,
            // so we treat these as partial events to be shown to the user in real-time.
            let type_name_lower = event_type.to_lowercase();
            let has_delta_field = obj.contains_key("delta")
                || obj.contains_key("partial")
                || obj.contains_key("chunk");
            let is_explicit_delta = type_name_lower.contains("delta")
                || type_name_lower.contains("partial")
                || type_name_lower.contains("chunk")
                || has_delta_field;

            if is_verbose {
                return format!("Partial event: {event_type}");
            }
            if is_explicit_delta {
                // In non-verbose mode, show explicit partial events (they're user content)
                // Extract full content (not truncated) for delta events
                let full_content: Option<String> = classification
                    .content_field
                    .as_ref()
                    .and_then(|content| {
                        // Use classifier's detected content field first
                        obj.get(content)
                            .and_then(|v| v.as_str())
                            .map(std::string::ToString::to_string)
                            .or_else(|| {
                                // Content field wasn't a string, try extracting nested text
                                obj.get(content).and_then(extract_nested_text)
                            })
                    })
                    .or_else(|| {
                        // Try delta field (common pattern)
                        obj.get("delta")
                            .and_then(|v| v.as_str())
                            .map(std::string::ToString::to_string)
                            .or_else(|| {
                                // Try nested delta.text or delta.content
                                obj.get("delta").and_then(|d| d.as_object()).and_then(|o| {
                                    o.get("text")
                                        .or_else(|| o.get("content"))
                                        .and_then(|t| t.as_str())
                                        .map(std::string::ToString::to_string)
                                })
                            })
                    })
                    .or_else(|| {
                        // Try common text fields at top level
                        ["text", "content", "message"].iter().find_map(|field| {
                            obj.get(*field)
                                .and_then(|val| val.as_str())
                                .map(|s| s.to_string())
                        })
                    });

                if let Some(content) = full_content {
                    if !content.trim().is_empty() {
                        return format!("{content}\n");
                    }
                }
                // Short content that looks like partial - don't show in non-verbose mode
                return String::new();
            }
            // Short content that looks like partial - don't show in non-verbose mode
            String::new()
        }
        StreamEventType::Control => {
            // Control events are state management - don't show in output
            String::new()
        }
        StreamEventType::Complete => {
            if is_verbose {
                format!("Complete event: {event_type}")
            } else {
                // In non-verbose mode, don't show complete events without content
                String::new()
            }
        }
    }
}

/// Extract common metadata fields from a JSON object.
///
/// Returns formatted field strings for display.
fn extract_common_fields(obj: &serde_json::Map<String, serde_json::Value>) -> Vec<String> {
    [
        "subtype",
        "session_id",
        "sessionID",
        "message_id",
        "messageID",
        "index",
        "reason",
        "status",
    ]
    .iter()
    .filter_map(|key| {
        let val = obj.get(*key)?;
        let val_str = match val {
            serde_json::Value::String(s) => {
                if s.chars().count() > 20 {
                    let truncated: String = s.chars().take(17).collect();
                    format!("{truncated}...")
                } else {
                    s.clone()
                }
            }
            serde_json::Value::Number(n) => n.to_string(),
            serde_json::Value::Bool(b) => b.to_string(),
            _ => return None,
        };
        Some(format!("{key}={val_str}"))
    })
    .collect()
}

/// Format an unknown JSON event for display in verbose/debug mode.
///
/// This is a generic handler for unknown events that works across all parsers.
/// It uses algorithmic classification to detect delta/partial events vs control events,
/// and extracts key fields from the JSON to provide useful debugging info.
///
/// # Arguments
/// * `line` - The raw JSON line
/// * `parser_name` - Name of the parser for display prefix
/// * `colors` - Colors struct for formatting
/// * `is_verbose` - Whether to show unknown events
///
/// # Returns
/// A formatted string showing the event type and key fields, or an empty string
/// if the JSON couldn't be parsed or verbosity should suppress it.
#[must_use]
pub fn format_unknown_json_event(
    line: &str,
    parser_name: &str,
    colors: crate::logger::Colors,
    is_verbose: bool,
) -> String {
    // Try to parse as generic JSON to extract type and key fields
    let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
        // Only show parsing failure message in verbose mode
        if is_verbose {
            return format!(
                "{}[{}]{} {}Unknown event (invalid JSON)\n",
                colors.dim(),
                parser_name,
                colors.reset(),
                colors.dim()
            );
        }
        return String::new();
    };

    let Some(obj) = value.as_object() else {
        if is_verbose {
            return format!(
                "{}[{}]{} {}Unknown event (non-object JSON)\n",
                colors.dim(),
                parser_name,
                colors.reset(),
                colors.dim()
            );
        }
        return String::new();
    };

    // Use stream classifier for algorithmic event detection
    let classifier = StreamEventClassifier::new();
    let classification = classifier.classify(&value);

    // Extract event type
    let (event_type, _) = extract_event_type_and_classify(obj, &value);

    // Build type label (may early-return String::new for suppression)
    let type_label = build_type_label(&event_type, obj, &classification, is_verbose);
    if type_label.ends_with('\n') {
        // Early return for explicit delta events with content (already formatted with newline)
        return type_label;
    }
    if type_label.is_empty() {
        // Suppressed event (control or empty partial)
        return String::new();
    }

    // For partial/delta events in verbose mode, extract and show content
    let content_info = if classification.event_type == StreamEventType::Partial && is_verbose {
        extract_partial_content(obj, &classification)
    } else {
        None
    };

    // Extract common fields for context
    let fields = extract_common_fields(obj);

    let fields_str = if fields.is_empty() {
        String::new()
    } else {
        format!(" ({})", fields.join(", "))
    };

    // Add content info if available
    let fields_str = if let Some(content) = content_info {
        format!("{fields_str}{content}")
    } else {
        fields_str
    };

    format!(
        "{}[{}]{} {}{}{}{}\n",
        colors.dim(),
        parser_name,
        colors.reset(),
        colors.dim(),
        type_label,
        fields_str,
        colors.reset()
    )
}
