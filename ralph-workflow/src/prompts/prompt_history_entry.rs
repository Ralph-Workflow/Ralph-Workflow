//! Prompt history entry type for reducer-owned prompt replay.
//!
//! [`PromptHistoryEntry`] stores a generated prompt alongside an optional
//! content-id that identifies the materialized inputs at the time of generation.
//! The content-id is used by [`get_stored_or_generate_prompt`] to detect
//! stale-content replay: if the current materialization content-id differs from
//! the stored one, the history entry is treated as a cache miss and a fresh
//! prompt is generated.
//!
//! # Backward Compatibility
//!
//! Old checkpoints stored prompt history as `HashMap<String, String>` (bare
//! strings). The custom [`Deserialize`] implementation for `PromptHistoryEntry`
//! accepts both formats:
//!
//! - **Legacy (v0):** `"some prompt text"` → `PromptHistoryEntry { content: "some prompt text", content_id: None }`
//! - **Current (v1):** `{"content":"...","content_id":"abc123"}` → full struct

use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// A stored prompt with optional content-id for stale-replay detection.
///
/// The `content_id` field, when `Some`, must match the content-id of the
/// current materialized prompt inputs before the entry is replayed. When
/// `None` (entries loaded from legacy checkpoints), replay is allowed only when
/// the caller does not provide a current content-id (i.e., `current_content_id = None`).
/// When the caller provides a current content-id, legacy entries are treated as
/// a cache miss to prevent stale prompt replay.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PromptHistoryEntry {
    /// The stored prompt text.
    pub content: String,
    /// SHA-256 hex digest of the materialized inputs at generation time.
    ///
    /// When `None`, the entry was created from a legacy checkpoint (v0 format)
    /// that did not record a content-id. When the caller provides a
    /// `current_content_id`, this entry is treated as a cache miss to avoid stale
    /// prompt replay.
    pub content_id: Option<String>,
}

impl PromptHistoryEntry {
    /// Create a new entry with content and optional content-id.
    #[must_use]
    pub const fn new(content: String, content_id: Option<String>) -> Self {
        Self {
            content,
            content_id,
        }
    }

    /// Create a legacy entry with no content-id (backward compat).
    #[must_use]
    pub const fn from_string(content: String) -> Self {
        Self {
            content,
            content_id: None,
        }
    }
}

// =========================================================================
// Serde implementation for backward compatibility
// =========================================================================
//
// Serializes as {"content":"...","content_id":"..."} (v1 format).
// Deserializes from either bare string (v0) or object (v1).

impl Serialize for PromptHistoryEntry {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut s = serializer.serialize_struct(
            "PromptHistoryEntry",
            if self.content_id.is_some() { 2 } else { 1 },
        )?;
        s.serialize_field("content", &self.content)?;
        if let Some(content_id) = &self.content_id {
            s.serialize_field("content_id", content_id)?;
        }
        s.end()
    }
}

/// Internal untagged representation for backward-compatible deserialization.
#[derive(Deserialize)]
#[serde(untagged)]
enum PromptHistoryEntryRepr {
    /// v0: bare string (legacy checkpoint format).
    Legacy(String),
    /// v1: full object with content and optional `content_id`.
    Current {
        content: String,
        #[serde(default)]
        content_id: Option<String>,
    },
}

impl<'de> Deserialize<'de> for PromptHistoryEntry {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        match PromptHistoryEntryRepr::deserialize(deserializer)? {
            PromptHistoryEntryRepr::Legacy(content) => Ok(Self {
                content,
                content_id: None,
            }),
            PromptHistoryEntryRepr::Current {
                content,
                content_id,
            } => Ok(Self {
                content,
                content_id,
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_serialize_round_trip() {
        let entry = PromptHistoryEntry {
            content: "my prompt".to_string(),
            content_id: Some("abc123".to_string()),
        };
        let json = serde_json::to_string(&entry).unwrap();
        let deserialized: PromptHistoryEntry = serde_json::from_str(&json).unwrap();
        assert_eq!(entry, deserialized);
    }

    #[test]
    fn test_serialize_with_none_content_id() {
        let entry = PromptHistoryEntry {
            content: "my prompt".to_string(),
            content_id: None,
        };
        let json = serde_json::to_string(&entry).unwrap();
        assert!(
            !json.contains("content_id"),
            "When content_id is None, serialization should omit the field; got: {json}"
        );
        let deserialized: PromptHistoryEntry = serde_json::from_str(&json).unwrap();
        assert_eq!(entry, deserialized);
    }

    #[test]
    fn test_deserialize_legacy_bare_string() {
        // v0 format: bare string in checkpoint
        let json = r#""some legacy prompt""#;
        let entry: PromptHistoryEntry = serde_json::from_str(json).unwrap();
        assert_eq!(entry.content, "some legacy prompt");
        assert_eq!(entry.content_id, None);
    }

    #[test]
    fn test_deserialize_v1_object_with_content_id() {
        let json = r#"{"content":"my prompt","content_id":"sha256abc"}"#;
        let entry: PromptHistoryEntry = serde_json::from_str(json).unwrap();
        assert_eq!(entry.content, "my prompt");
        assert_eq!(entry.content_id.as_deref(), Some("sha256abc"));
    }

    #[test]
    fn test_deserialize_v1_object_without_content_id() {
        let json = r#"{"content":"my prompt"}"#;
        let entry: PromptHistoryEntry = serde_json::from_str(json).unwrap();
        assert_eq!(entry.content, "my prompt");
        assert_eq!(entry.content_id, None);
    }

    #[test]
    fn test_hashmap_deserialize_from_legacy_format() {
        // Simulate a v0 checkpoint: HashMap<String, String> stored as
        // {"key": "prompt text"} in JSON. With our custom deserializer,
        // it should load as PromptHistoryEntry { content: "prompt text", content_id: None }.
        let json = r#"{"planning_1":"some plan prompt","development_1":"some dev prompt"}"#;
        let map: std::collections::HashMap<String, PromptHistoryEntry> =
            serde_json::from_str(json).unwrap();
        assert_eq!(map.len(), 2);
        assert_eq!(map["planning_1"].content, "some plan prompt");
        assert_eq!(map["planning_1"].content_id, None);
        assert_eq!(map["development_1"].content, "some dev prompt");
    }
}
