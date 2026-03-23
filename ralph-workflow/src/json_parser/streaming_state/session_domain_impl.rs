impl StreamingSession {
    pub(super) fn compute_content_hash(&self) -> Option<u64> {
        compute_content_hash_from_accumulated(&self.accumulated)
    }

    fn classify_content_for_dedup(content: &str, has_text: bool) -> (bool, bool) {
        let has_tool_use = content.contains("TOOL_USE:");
        (has_tool_use, has_tool_use && has_text)
    }

    #[must_use]
    pub fn is_duplicate_by_hash(
        &self,
        content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        let has_text = self.accumulated.iter().any(|((ct, _), v)| *ct == ContentType::Text && !v.is_empty());
        let (has_tool_use, is_mixed) = Self::classify_content_for_dedup(content, has_text);
        if is_mixed { return self.is_duplicate_mixed_content(content, tool_name_hints); }
        if has_tool_use { return self.is_duplicate_tool_use(content, tool_name_hints); }
        is_duplicate_text_content(&self.accumulated, content)
    }

    fn is_duplicate_mixed_content(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        normalized_content
            == build_mixed_content_reconstruction(
                &self.accumulated,
                &self.tool_names,
                tool_name_hints,
            )
    }

    fn is_duplicate_tool_use(
        &self,
        normalized_content: &str,
        tool_name_hints: Option<&std::collections::HashMap<usize, String>>,
    ) -> bool {
        let reconstructed =
            build_tool_use_reconstruction(&self.accumulated, &self.tool_names, tool_name_hints);

        if reconstructed.is_empty() {
            return false;
        }

        normalized_content == reconstructed
    }

    #[must_use]
    pub fn is_likely_snapshot(&self, text: &str, key: &str) -> bool {
        is_likely_snapshot(&self.accumulated, text, key)
    }

    pub fn extract_delta_from_snapshot(
        &self,
        text: &str,
        key: &str,
    ) -> Result<usize, SnapshotDeltaError> {
        extract_delta_from_snapshot(&self.accumulated, text, key)
    }

    pub fn get_delta_from_snapshot<'a>(
        &self,
        text: &'a str,
        key: &str,
    ) -> Result<&'a str, SnapshotDeltaError> {
        let delta_len = self.extract_delta_from_snapshot(text, key)?;
        Ok(&text[delta_len..])
    }

    #[must_use]
    pub fn get_streaming_quality_metrics(
        &self,
    ) -> crate::json_parser::health::StreamingQualityMetrics {
        let all_sizes = self.delta_sizes.values().flat_map(|v| v.iter().copied());
        let metrics = crate::json_parser::health::StreamingQualityMetrics::from_sizes(all_sizes);

        crate::json_parser::health::StreamingQualityMetrics {
            snapshot_repairs_count: self.snapshot_repairs_count,
            large_delta_count: self.large_delta_count,
            protocol_violations: self.protocol_violations,
            ..metrics
        }
    }
}
