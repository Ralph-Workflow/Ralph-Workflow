impl StreamingSession {
    #[must_use]
    pub fn is_likely_snapshot(&self, text: &str, key: &str) -> bool {
        let content_key = (ContentType::Text, key.to_string());

        // Check if we have accumulated content for this key
        if let Some(previous) = self.accumulated.get(&content_key) {
            // Use DeltaDeduplicator with threshold-aware snapshot detection
            // This prevents false positives by requiring strong overlap (>=30 chars, >=50% ratio)
            return DeltaDeduplicator::is_likely_snapshot_with_thresholds(text, previous);
        }

        false
    }

    /// Extract the delta portion from a snapshot.
    ///
    /// When a snapshot is detected (full accumulated content sent as a "delta"),
    /// this method extracts only the new portion that hasn't been accumulated yet.
    ///
    /// # Arguments
    /// * `text` - The snapshot text (full accumulated content + new content)
    /// * `key` - The content key to compare against
    ///
    /// # Returns
    /// * `Ok(usize)` - The length of the delta portion (new content only)
    /// * `Err` - If the text is not actually a snapshot (doesn't start with accumulated content)
    ///
    /// # Note
    /// Returns the length of the delta portion as `usize` since we can't return
    /// a reference to `text` with the correct lifetime. Callers can slice `text`
    /// themselves using `&text[delta_len..]`.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn extract_delta_from_snapshot(&self, text: &str, key: &str) -> Result<usize, String> {
        let content_key = (ContentType::Text, key.to_string());

        if let Some(previous) = self.accumulated.get(&content_key) {
            // Use DeltaDeduplicator with threshold-aware delta extraction
            // This ensures we only extract when overlap meets strong criteria
            if let Some(new_content) =
                DeltaDeduplicator::extract_new_content_with_thresholds(text, previous)
            {
                // Calculate the position where new content starts
                let delta_start = text.len() - new_content.len();
                return Ok(delta_start);
            }
        }

        // If we get here, the text wasn't actually a snapshot
        // This could indicate a false positive from is_likely_snapshot
        Err(format!(
            "extract_delta_from_snapshot called on non-snapshot text. \
            key={key:?}, text={text:?}. Snapshot detection may have had a false positive."
        ))
    }

    /// Get the delta portion as a string slice from a snapshot.
    ///
    /// This is a convenience wrapper that returns the actual substring
    /// instead of just the length.
    ///
    /// # Returns
    /// * `Ok(&str)` - The delta portion (new content only)
    /// * `Err` - If the text is not actually a snapshot
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn get_delta_from_snapshot<'a>(&self, text: &'a str, key: &str) -> Result<&'a str, String> {
        let delta_len = self.extract_delta_from_snapshot(text, key)?;
        Ok(&text[delta_len..])
    }

    /// Get streaming quality metrics for the current session.
    ///
    /// Returns aggregated metrics about delta sizes and streaming patterns
    /// during the session. This is useful for debugging and analyzing
    /// streaming behavior.
    ///
    /// # Returns
    /// Aggregated metrics across all content types and keys.
    #[must_use]
    pub fn get_streaming_quality_metrics(&self) -> StreamingQualityMetrics {
        // Flatten all delta sizes across all content types and keys
        let all_sizes = self.delta_sizes.values().flat_map(|v| v.iter().copied());
        let metrics = StreamingQualityMetrics::from_sizes(all_sizes);

        // Add session-level metrics
        StreamingQualityMetrics {
            snapshot_repairs_count: self.snapshot_repairs_count,
            large_delta_count: self.large_delta_count,
            protocol_violations: self.protocol_violations,
            ..metrics
        }
    }
}
