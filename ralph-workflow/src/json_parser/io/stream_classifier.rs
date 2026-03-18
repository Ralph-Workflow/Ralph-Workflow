//! Stream event classifier for algorithmic detection of partial vs complete events.
//!
//! This module provides a classifier that can distinguish between different types
//! of streaming events without prior knowledge of the specific protocol. It uses
//! heuristics based on JSON structure and field names to make conservative decisions
//! about event classification.

use serde_json::Value;

/// Classification of a streaming event
///
/// Represents the nature of a streaming event to inform how it should be
/// processed and displayed to the user.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamEventType {
    /// Partial/delta content that should be accumulated
    ///
    /// These events contain incremental updates that need to be combined
    /// with other events to form complete content.
    Partial,

    /// Complete, self-contained content
    ///
    /// These events contain full content that can be displayed independently.
    Complete,

    /// Control/metadata event
    ///
    /// These events provide session information (start/stop) or metadata
    /// but don't contain user-facing content.
    Control,
}

/// Result of event classification
///
/// Contains the classification along with extracted metadata about the event.
#[derive(Debug, Clone)]
pub struct ClassificationResult {
    /// The classified event type
    pub event_type: StreamEventType,
    /// Detected event type name (e.g., "message", "delta", "error")
    pub type_name: Option<String>,
    /// The primary content field if found
    pub content_field: Option<String>,
}

/// Stream event classifier
///
/// Analyzes JSON events to determine if they represent partial content,
/// complete messages, or control events. Uses conservative heuristics to
/// prefer showing content over hiding it.
pub struct StreamEventClassifier {
    /// Threshold for considering text content "substantial" enough to be complete
    substantial_content_threshold: usize,
}

impl Default for StreamEventClassifier {
    fn default() -> Self {
        Self::new()
    }
}

impl StreamEventClassifier {
    /// Create a new classifier with default settings
    pub const fn new() -> Self {
        Self {
            substantial_content_threshold: 50,
        }
    }

    /// Classify a JSON event
    ///
    /// # Arguments
    /// * `value` - The parsed JSON value to classify
    ///
    /// # Returns
    /// A `ClassificationResult` with the detected event type and metadata
    pub fn classify(&self, value: &Value) -> ClassificationResult {
        // Extract the object if present
        let Some(obj) = value.as_object() else {
            return ClassificationResult {
                event_type: StreamEventType::Complete,
                type_name: None,
                content_field: None,
            };
        };

        // Extract the type field
        let type_name = obj
            .get("type")
            .or_else(|| obj.get("event_type"))
            .and_then(|v| v.as_str())
            .map(std::string::ToString::to_string);

        // Check for explicit delta flag
        let is_delta = obj
            .get("delta")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);

        // Check for control event patterns
        if Self::is_control_event(type_name.as_ref(), obj) {
            return ClassificationResult {
                event_type: StreamEventType::Control,
                type_name,
                content_field: None,
            };
        }

        // Check for partial/delta indicators
        if self.is_partial_event(type_name.as_ref(), obj, is_delta) {
            return ClassificationResult {
                event_type: StreamEventType::Partial,
                type_name,
                content_field: Self::find_content_field(obj),
            };
        }

        // Default to complete for unknown events
        // (prefer showing content over hiding it)
        ClassificationResult {
            event_type: StreamEventType::Complete,
            type_name,
            content_field: Self::find_content_field(obj),
        }
    }

    /// Check if an event is a control/metadata event
    fn is_control_event(type_name: Option<&String>, obj: &serde_json::Map<String, Value>) -> bool {
        // Check type name for control patterns
        if let Some(name) = type_name {
            let control_patterns = [
                "start",
                "started",
                "init",
                "initialize",
                "stop",
                "stopped",
                "end",
                "done",
                "complete",
                "error",
                "fail",
                "failed",
                "failure",
                "ping",
                "pong",
                "heartbeat",
                "keepalive",
                "metadata",
                "meta",
            ];

            let name_lower = name.to_lowercase();
            if control_patterns
                .iter()
                .any(|pattern| name_lower.contains(pattern))
            {
                return true;
            }
        }

        // Check for status/error fields without content
        let has_status = obj.contains_key("status") || obj.contains_key("error");
        let has_content = Self::has_content_field(obj);
        has_status && !has_content
    }

    /// Check if an event is a partial/delta event
    fn is_partial_event(
        &self,
        type_name: Option<&String>,
        obj: &serde_json::Map<String, Value>,
        explicit_delta: bool,
    ) -> bool {
        // Explicit delta flag
        if explicit_delta {
            return true;
        }

        // Check type name for partial patterns
        if let Some(name) = type_name {
            let partial_patterns = [
                "delta",
                "partial",
                "increment",
                "chunk",
                "progress",
                "streaming",
                "update",
            ];

            let name_lower = name.to_lowercase();
            if partial_patterns
                .iter()
                .any(|pattern| name_lower.contains(pattern))
            {
                return true;
            }
        }

        // Check for delta fields in the object (content fields, not boolean flags)
        // Only treat as partial if the field contains actual content (string, array, or object),
        // not if it's just a boolean flag or null
        let delta_fields = ["delta", "partial", "increment"];
        if delta_fields.iter().any(|field| {
            obj.get(*field).is_some_and(|value| {
                value.is_string()
                    || value.is_array()
                    || value.is_object()
                    || (value.is_number() && value.as_i64() != Some(0))
            })
        }) {
            return true;
        }

        // Check for small content fragments that might be partial
        // Only apply this heuristic if there's no explicit delta flag or type name
        if !explicit_delta
            && (type_name.is_none()
                || !type_name.as_ref().is_some_and(|n| {
                    let n_lower = n.to_lowercase();
                    n_lower.contains("delta")
                        || n_lower.contains("partial")
                        || n_lower.contains("chunk")
                }))
        {
            if let Some(content) = Self::find_content_field(obj) {
                if let Some(text) = obj.get(&content).and_then(|v| v.as_str()) {
                    // Short text fragments are likely partial, BUT check for complete patterns
                    if text.len() < self.substantial_content_threshold {
                        let text_lower = text.to_lowercase();
                        let trimmed = text.trim();

                        // Check for complete message indicators:
                        // 1. Common response words that are complete on their own
                        let complete_responses = [
                            "ok",
                            "okay",
                            "yes",
                            "no",
                            "true",
                            "false",
                            "done",
                            "finished",
                            "complete",
                            "success",
                            "failed",
                            "error",
                            "warning",
                            "info",
                            "debug",
                            "pending",
                            "processing",
                            "running",
                            "none",
                            "null",
                            "empty",
                        ];
                        let is_complete_response = complete_responses.contains(&trimmed);

                        // 2. Messages ending with terminal punctuation
                        let ends_with_terminal = trimmed.ends_with('.')
                            || trimmed.ends_with('!')
                            || trimmed.ends_with('?');

                        // 3. Messages containing newlines (usually intentional formatting)
                        let has_newline = text.contains('\n');

                        // 4. Error/warning patterns (these are complete messages)
                        let is_error_message = text_lower.contains("error:")
                            || text_lower.contains("warning:")
                            || text_lower.starts_with("error")
                            || text_lower.starts_with("warning");

                        // If any complete indicator is present, it's NOT partial
                        if is_complete_response
                            || ends_with_terminal
                            || has_newline
                            || is_error_message
                        {
                            return false;
                        }

                        // Otherwise, short text without clear completion markers is likely partial
                        return true;
                    }
                }
            }
        }

        false
    }

    /// Find the primary content field in an object
    fn find_content_field(obj: &serde_json::Map<String, Value>) -> Option<String> {
        // Common content field names in priority order
        let content_fields = [
            "content",
            "text",
            "message",
            "data",
            "output",
            "result",
            "response",
            "body",
            "thinking",
            "reasoning",
            "delta",
        ];

        content_fields
            .iter()
            .find(|field| {
                obj.get(**field)
                    .is_some_and(|v| matches!(v, Value::String(_)))
            })
            .map(|f| f.to_string())
    }

    /// Check if an object has any content field
    fn has_content_field(obj: &serde_json::Map<String, Value>) -> bool {
        Self::find_content_field(obj).is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_classify_delta_event() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"}
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Partial);
    }

    #[test]
    fn test_classify_control_event() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "message_start",
            "message": {"id": "msg_123"}
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Control);
    }

    #[test]
    fn test_classify_complete_message() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "message",
            "content": "This is a complete message with substantial content that should be displayed as is."
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Complete);
    }

    #[test]
    fn test_classify_explicit_delta_flag() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "message",
            "delta": true,
            "content": "partial"
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Partial);
    }

    #[test]
    fn test_classify_error_event() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "error",
            "message": "Something went wrong"
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Control);
    }

    #[test]
    fn test_small_content_is_partial() {
        let classifier = StreamEventClassifier::new();
        let event = json!({
            "type": "chunk",
            "text": "Hi"
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Partial);
    }

    #[test]
    fn test_substantial_content_is_complete() {
        let classifier = StreamEventClassifier::new();
        let long_text = "This is a substantial message that exceeds the threshold and should be considered complete.".repeat(2);
        let event = json!({
            "type": "message",
            "content": long_text
        });

        let result = classifier.classify(&event);
        assert_eq!(result.event_type, StreamEventType::Complete);
    }
}
