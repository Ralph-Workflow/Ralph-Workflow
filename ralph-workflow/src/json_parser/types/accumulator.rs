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
    pub(crate) fn add_delta(self, content_type: ContentType, key: &str, delta: &str) -> Self {
        let composite_key = (content_type, key.to_string());

        if let Some(buf) = self.buffers.get(&composite_key) {
            if buf.len() < MAX_BUFFER_SIZE {
                let remaining = MAX_BUFFER_SIZE.saturating_sub(buf.len());
                let new_buf = if delta.len() <= remaining {
                    format!("{}{}", buf, delta)
                } else if remaining > 0 {
                    format!("{}{}", buf, &delta[..remaining])
                } else {
                    buf.clone()
                };
                let new_buffers: HashMap<_, _> = self
                    .buffers
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .chain(std::iter::once((composite_key.clone(), new_buf)))
                    .collect();
                return Self {
                    buffers: new_buffers,
                    key_order: self.key_order,
                };
            }
        }

        let new_value = if delta.len() <= MAX_BUFFER_SIZE {
            delta.to_string()
        } else {
            delta[..MAX_BUFFER_SIZE].to_string()
        };
        let new_key_order = if self.key_order.contains(&composite_key) {
            self.key_order.clone()
        } else {
            self.key_order
                .iter()
                .cloned()
                .chain(std::iter::once(composite_key))
                .collect()
        };
        Self {
            buffers: self
                .buffers
                .iter()
                .chain([(composite_key.clone(), new_value)])
                .map(|((ct, s), v)| ((*ct, s.clone()), v.clone()))
                .collect(),
            key_order: new_key_order,
        }
    }

    /// Get accumulated content for a specific content type and key.
    pub(crate) fn get(&self, content_type: ContentType, key: &str) -> Option<&str> {
        self.buffers
            .get(&(content_type, key.to_string()))
            .map(std::string::String::as_str)
    }

    /// Clear all accumulated content.
    pub(crate) fn clear(self) -> Self {
        Self {
            buffers: std::collections::HashMap::new(),
            key_order: Vec::new(),
        }
    }

    pub(crate) fn clear_key(self, content_type: ContentType, key: &str) -> Self {
        let composite_key = (content_type, key.to_string());
        Self {
            buffers: self
                .buffers
                .into_iter()
                .filter(|(k, _)| *k != composite_key)
                .collect(),
            key_order: self
                .key_order
                .into_iter()
                .filter(|k| *k != composite_key)
                .collect(),
        }
    }

    /// Check if there is any accumulated content (used in tests).
    #[cfg(test)]
    pub(crate) fn is_empty(&self) -> bool {
        self.buffers.is_empty()
    }
}
