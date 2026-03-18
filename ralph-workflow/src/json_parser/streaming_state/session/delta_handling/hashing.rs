impl StreamingSession {
    pub(super) fn compute_content_hash(&self) -> Option<u64> {
        if self.accumulated.is_empty() {
            return None;
        }

        // Collect and sort keys for consistent hashing
        // Sort by numeric index (not lexicographic) to preserve actual message order.
        // This is critical for correctness when indices don't sort lexicographically
        // (e.g., 0, 1, 10, 2 should sort as 0, 1, 2, 10).
        let mut keys: Vec<_> = self.accumulated.keys().collect();
        keys.sort_by_key(|k| {
            let type_order = match k.0 {
                ContentType::Text => 0,
                ContentType::ToolInput => 1,
                ContentType::Thinking => 2,
            };
            let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
            (index, type_order)
        });

        let hash = keys.iter().fold(0u64, |acc, key| {
            let content = self.accumulated.get(key);
            let key_hash = format!("{:?}-{}", key.0, key.1);
            let mut hasher = std::collections::hash_map::DefaultHasher::new();
            key_hash.hash(&mut hasher);
            content.hash(&mut hasher);
            acc.wrapping_add(hasher.finish())
        });

        Some(hash)
    }

    /// Check if content matches the previously streamed content by hash.
    ///
    /// This is a more precise alternative to `has_any_streamed_content()` for
    /// deduplication. Instead of checking if ANY content was streamed, this checks
    /// if the EXACT content was streamed by comparing hashes.
    ///
    /// This method looks at ALL accumulated content across all content types and indices.
    /// If the combined accumulated content matches the input, it returns true.
    ///
    /// # Arguments
    /// * `content` - The content to check (typically normalized content from assistant events)
    /// * `tool_name_hints` - Optional tool names from assistant event (by content block index)
    ///
    /// # Returns
    /// * `true` - The content hash matches the previously streamed content
    /// * `false` - The content is different or no content was streamed
    #[must_use]
    pub fn is_duplicate_by_hash(
        &self,
        content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        // Check if content contains both text and tool_use markers (mixed content)
        let has_tool_use = content.contains("TOOL_USE:");
        let has_text = self
            .accumulated
            .iter()
            .any(|((ct, _), v)| *ct == ContentType::Text && !v.is_empty());

        if has_tool_use && has_text {
            // Mixed content: both text and tool_use need to be checked
            // Reconstruct the full normalized content from both text and tool_use
            return self.is_duplicate_mixed_content(content, tool_name_hints);
        } else if has_tool_use {
            // Only tool_use content
            return self.is_duplicate_tool_use(content, tool_name_hints);
        }

        // Only text content - check if the input content matches ALL accumulated text content combined
        // This handles the case where assistant events arrive during streaming (before message_stop)
        // We collect and combine all text content in index order
        let text_keys: Vec<_> = self
            .accumulated
            .keys()
            .filter(|(ct, _)| *ct == ContentType::Text)
            .collect();
        // Sort by numeric index (not lexicographic) to preserve actual message order
        // This is critical for correctness when indices don't sort lexicographically (e.g., 0, 1, 10, 2)
        // unwrap_or(u64::MAX) handles non-numeric indices by sorting them last; this should
        // never happen in practice since GLM always sends numeric string indices, but if
        // it does occur, it indicates a protocol violation and will be visible in output
        let mut sorted_text_keys = text_keys;
        sorted_text_keys.sort_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX));

        // Combine all accumulated text content in sorted order
        let combined_content: String = sorted_text_keys
            .iter()
            .filter_map(|key| self.accumulated.get(key))
            .cloned()
            .collect();

        // Direct string comparison is more reliable than hashing
        // because hashing can have collisions and we want exact match
        combined_content == content
    }

    /// Check if mixed content (text + `tool_use`) from an assistant event matches accumulated content.
    ///
    /// This handles the case where assistant events contain both text and `tool_use` blocks.
    /// We reconstruct the full normalized content from both text and `tool_use` accumulated content
    /// and compare it against the assistant event content.
    ///
    /// # Arguments
    /// * `normalized_content` - Content potentially containing both text and "`TOOL_USE:{name}:{input`}" markers
    /// * `tool_name_hints` - Optional tool names from assistant event (by content block index)
    ///
    /// # Returns
    /// * `true` - All content (text + `tool_use`) matches accumulated content
    /// * `false` - Content differs or not accumulated yet
    fn is_duplicate_mixed_content(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        // Collect all content blocks in order (text and tool_use interleaved)
        // We need to reconstruct the exact order as it appears in the assistant event

        // Get all accumulated content keys and sort by index
        let mut all_keys: Vec<_> = self.accumulated.keys().collect();
        all_keys.sort_by_key(|k| {
            // Sort by index first (to preserve actual order), then by content type as tiebreaker
            let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
            let type_order = match k.0 {
                ContentType::Text => 0,
                ContentType::ToolInput => 1,
                ContentType::Thinking => 2,
            };
            (index, type_order)
        });

        let reconstructed: String = all_keys
            .iter()
            .filter_map(|&(ref ct, ref index_str)| {
                let accumulated_content = self.accumulated.get(&(*ct, index_str.clone()))?;
                match ct {
                    ContentType::Text => {
                        // Text content is added as-is
                        Some(accumulated_content.clone())
                    }
                    ContentType::ToolInput => {
                        // Tool_use content needs normalization with tool name
                        let index_num = index_str.parse::<u64>().unwrap_or(0);
                        let tool_name = usize::try_from(index_num)
                            .ok()
                            .and_then(|idx| {
                                tool_name_hints.and_then(|hints| {
                                    hints.get(&idx).map(std::string::String::as_str)
                                })
                            })
                            .or_else(|| self.tool_names.get(&index_num).and_then(|n| n.as_deref()))
                            .unwrap_or("");

                        // Normalize: "TOOL_USE:{name}:{input}"
                        Some(format!("TOOL_USE:{tool_name}:{accumulated_content}"))
                    }
                    ContentType::Thinking => {
                        // Thinking content - not currently used in assistant events
                        // but included for completeness
                        None
                    }
                }
            })
            .collect();

        // Check if the reconstructed content matches the input
        normalized_content == reconstructed
    }

    /// Check if `tool_use` content from an assistant event matches accumulated `ToolInput`.
    ///
    /// Assistant events may contain normalized `tool_use` blocks (with "`TOOL_USE`:" prefix).
    /// This method reconstructs the normalized representation from accumulated content
    /// and checks if it matches the assistant event content.
    ///
    /// # Arguments
    /// * `normalized_content` - Content potentially containing "`TOOL_USE:{name}:{input`}" markers
    /// * `tool_name_hints` - Optional tool names from assistant event (by content block index)
    ///
    /// # Returns
    /// * `true` - All `tool_use` blocks match accumulated content
    /// * `false` - `Tool_use` content differs or not accumulated yet
    fn is_duplicate_tool_use(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        // Get all ToolInput keys and sort by index
        let mut tool_keys: Vec<_> = self
            .accumulated
            .keys()
            .filter(|(ct, _)| *ct == ContentType::ToolInput)
            .collect();
        tool_keys.sort_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX));

        let reconstructed: String = tool_keys
            .iter()
            .filter_map(|&(ref ct, ref index_str)| {
                let accumulated_input = self.accumulated.get(&(*ct, index_str.clone()))?;
                // Get the tool name from hints first (from assistant event), then from tracking
                let index_num = index_str.parse::<u64>().unwrap_or(0);
                let tool_name = usize::try_from(index_num)
                    .ok()
                    .and_then(|idx| {
                        tool_name_hints
                            .and_then(|hints| hints.get(&idx).map(std::string::String::as_str))
                    })
                    .or_else(|| self.tool_names.get(&index_num).and_then(|n| n.as_deref()))
                    .unwrap_or("");

                // Normalize: "TOOL_USE:{name}:{input}"
                Some(format!("TOOL_USE:{tool_name}:{accumulated_input}"))
            })
            .collect();

        // Check if the reconstructed content matches the input
        if reconstructed.is_empty() {
            return false;
        }

        normalized_content == reconstructed
    }
}
