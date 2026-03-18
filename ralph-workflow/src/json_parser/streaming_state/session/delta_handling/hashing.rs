impl StreamingSession {
    pub(super) fn compute_content_hash(&self) -> Option<u64> {
        if self.accumulated.is_empty() {
            return None;
        }

        use itertools::Itertools;
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let hash = self
            .accumulated
            .keys()
            .sorted_by_key(|k| {
                let type_order = match k.0 {
                    ContentType::Text => 0,
                    ContentType::ToolInput => 1,
                    ContentType::Thinking => 2,
                };
                let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
                (index, type_order)
            })
            .fold(0u64, |acc, key| {
                let content = self.accumulated.get(key);
                let key_hash = format!("{:?}-{}", key.0, key.1);
                let key_hash_bytes = key_hash.as_bytes();
                let content_bytes = content.as_bytes();
                let hasher = DefaultHasher::default();
                let mut h = hasher;
                key_hash_bytes.hash(&mut h);
                content_bytes.hash(&mut h);
                acc.wrapping_add(h.finish())
            });

        Some(hash)
    }

    /// Check if content matches the previously streamed content by hash.
    #[must_use]
    pub fn is_duplicate_by_hash(
        &self,
        content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        use itertools::Itertools;

        let has_tool_use = content.contains("TOOL_USE:");
        let has_text = self
            .accumulated
            .iter()
            .any(|((ct, _), v)| *ct == ContentType::Text && !v.is_empty());

        if has_tool_use && has_text {
            return self.is_duplicate_mixed_content(content, tool_name_hints);
        } else if has_tool_use {
            return self.is_duplicate_tool_use(content, tool_name_hints);
        }

        let combined_content: String = self
            .accumulated
            .keys()
            .filter(|(ct, _)| *ct == ContentType::Text)
            .sorted_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX))
            .filter_map(|key| self.accumulated.get(key))
            .cloned()
            .collect();

        combined_content == content
    }

    fn is_duplicate_mixed_content(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        use itertools::Itertools;

        let reconstructed: String = self
            .accumulated
            .keys()
            .sorted_by_key(|k| {
                let index = k.1.parse::<u64>().unwrap_or(u64::MAX);
                let type_order = match k.0 {
                    ContentType::Text => 0,
                    ContentType::ToolInput => 1,
                    ContentType::Thinking => 2,
                };
                (index, type_order)
            })
            .filter_map(|(ct, index_str)| {
                let accumulated_content = self.accumulated.get(&(*ct, index_str.clone()))?;
                match ct {
                    ContentType::Text => Some(accumulated_content.clone()),
                    ContentType::ToolInput => {
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
                        Some(format!("TOOL_USE:{tool_name}:{accumulated_content}"))
                    }
                    ContentType::Thinking => None,
                }
            })
            .collect();

        normalized_content == reconstructed
    }

    fn is_duplicate_tool_use(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        use itertools::Itertools;

        let reconstructed: String = self
            .accumulated
            .keys()
            .filter(|(ct, _)| *ct == ContentType::ToolInput)
            .sorted_by_key(|k| k.1.parse::<u64>().unwrap_or(u64::MAX))
            .filter_map(|(ct, index_str)| {
                let accumulated_input = self.accumulated.get(&(*ct, index_str.clone()))?;
                let index_num = index_str.parse::<u64>().unwrap_or(0);
                let tool_name = usize::try_from(index_num)
                    .ok()
                    .and_then(|idx| {
                        tool_name_hints
                            .and_then(|hints| hints.get(&idx).map(std::string::String::as_str))
                    })
                    .or_else(|| self.tool_names.get(&index_num).and_then(|n| n.as_deref()))
                    .unwrap_or("");
                Some(format!("TOOL_USE:{tool_name}:{accumulated_input}"))
            })
            .collect();

        if reconstructed.is_empty() {
            return false;
        }

        normalized_content == reconstructed
    }
}
