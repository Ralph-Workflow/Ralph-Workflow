use serde::{Deserialize, Serialize};

/// Content type for delta accumulation.
///
/// Distinguishes between different types of content that may be streamed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum ContentType {
    /// Regular text content.
    Text,
    /// Thinking/reasoning content.
    Thinking,
    /// Tool input content.
    ToolInput,
}

/// Maximum buffer size per key to prevent unbounded memory growth.
const MAX_BUFFER_SIZE: usize = 10 * 1024 * 1024; // 10MB per key

/// Delta accumulator for streaming content.
///
/// Tracks partial content across multiple streaming events, accumulating
/// deltas for different content types. Uses a composite key approach
/// to track content by (`content_type`, key).
///
/// Supports both index-based tracking (for parsers with numeric indices)
/// and string-based key tracking (for parsers with string identifiers).
///
/// # Memory Safety
///
/// Each buffer has a maximum size of 10MB to prevent memory exhaustion
/// in long-running sessions. When a buffer exceeds this limit, new deltas
/// are ignored for that key.
///
/// # Design
///
/// This type uses immutable patterns - methods that modify state return
/// new values rather than mutating in place.
#[derive(Debug, Default, Clone)]
pub struct DeltaAccumulator {
    /// Accumulated content by (`content_type`, key) composite key.
    /// Using a String key to support both numeric and string-based identifiers.
    buffers: std::collections::HashMap<(ContentType, String), String>,
    /// Track the order of keys for `most_recent` operations.
    key_order: Vec<(ContentType, String)>,
}

impl DeltaAccumulator {
    /// Create a new delta accumulator.
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Add a delta for a specific content type and key.
    ///
    /// This is the generic method that supports both index-based and
    /// string-based key tracking. Enforces `MAX_BUFFER_SIZE` to prevent
    /// unbounded memory growth.
    pub(crate) fn add_delta(&mut self, content_type: ContentType, key: &str, delta: &str) {
        let composite_key = (content_type, key.to_string());

        if let Some(buf) = self.buffers.get_mut(&composite_key) {
            if buf.len() < MAX_BUFFER_SIZE {
                let remaining = MAX_BUFFER_SIZE.saturating_sub(buf.len());
                if delta.len() <= remaining {
                    buf.push_str(delta);
                } else if remaining > 0 {
                    buf.push_str(&delta[..remaining]);
                }
            }
        } else {
            let new_value = if delta.len() <= MAX_BUFFER_SIZE {
                delta.to_string()
            } else {
                delta[..MAX_BUFFER_SIZE].to_string()
            };
            self.buffers.insert(composite_key.clone(), new_value);
            if !self.key_order.contains(&composite_key) {
                self.key_order.push(composite_key);
            }
        }
    }

    /// Get accumulated content for a specific content type and key.
    pub(crate) fn get(&self, content_type: ContentType, key: &str) -> Option<&str> {
        self.buffers
            .get(&(content_type, key.to_string()))
            .map(std::string::String::as_str)
    }

    /// Clear all accumulated content.
    pub(crate) fn clear(&mut self) {
        self.buffers.clear();
        self.key_order.clear();
    }

    /// Clear content for a specific content type and key.
    pub(crate) fn clear_key(&mut self, content_type: ContentType, key: &str) {
        let composite_key = (content_type, key.to_string());
        self.buffers.remove(&composite_key);
        self.key_order.retain(|k| *k != composite_key);
    }

    /// Check if there is any accumulated content (used in tests).
    #[cfg(test)]
    pub(crate) fn is_empty(&self) -> bool {
        self.buffers.is_empty()
    }
}
