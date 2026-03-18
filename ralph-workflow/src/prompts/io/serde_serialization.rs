//! Serde serialization for prompt history types.
//!
//! This module contains manual Serialize implementations that inherently require
//! mutable state per serde's API. Placed in io/ boundary module to satisfy
//! the forbid_mut_binding lint.

use super::super::prompt_history_entry::PromptHistoryEntry;
use serde::{Serialize, Serializer};

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
