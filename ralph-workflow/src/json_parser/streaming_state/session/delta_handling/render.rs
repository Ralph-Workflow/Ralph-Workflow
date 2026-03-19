impl StreamingSession {
    pub fn get_accumulated(&self, content_type: ContentType, index: &str) -> Option<&str> {
        self.accumulated
            .get(&(content_type, index.to_string()))
            .map(std::string::String::as_str)
    }

    fn compute_hash<T: std::hash::Hash>(value: &T) -> u64 {
        let hasher = std::collections::hash_map::DefaultHasher::new();
        value.hash(&hasher);
        hasher.finish()
    }

    /// Return the set of accumulated keys for a given content type.
    ///
    /// This is used by non-TTY flush logic to render the final accumulated content
    /// once at a completion boundary (e.g., `message_stop`) without relying on
    /// arbitrary index bounds.
    #[must_use]
    pub fn accumulated_keys(&self, content_type: ContentType) -> Vec<String> {
        let mut keys: Vec<String> = self
            .accumulated
            .keys()
            .filter(|(ty, _key)| *ty == content_type)
            .map(|(_ty, key)| key.clone())
            .collect();

        // Prefer deterministic output. Many protocols use numeric indices; sort numerically
        // when possible, otherwise fall back to lexicographic sorting.
        keys.sort_by(|a, b| {
            let a_num = a.parse::<u64>();
            let b_num = b.parse::<u64>();
            match (a_num, b_num) {
                (Ok(a), Ok(b)) => a.cmp(&b),
                _ => a.cmp(b),
            }
        });

        keys.dedup();
        keys
    }

    /// Mark content as having been rendered (HashMap-based tracking).
    ///
    /// This should be called after rendering to update the per-key tracking.
    ///
    /// # Arguments
    /// * `content_type` - The type of content
    /// * `index` - The content index (as string for flexibility)
    pub fn mark_rendered(&mut self, content_type: ContentType, index: &str) {
        let content_key = (content_type, index.to_string());

        // Store the current accumulated content as last rendered
        if let Some(current) = self.accumulated.get(&content_key) {
            self.last_rendered.insert(content_key, current.clone());
        }
    }

    /// Check if content has been rendered before using hash-based tracking.
    ///
    /// This provides global duplicate detection across all content by computing
    /// a hash of the accumulated content and checking if it's in the rendered set.
    /// This is preserved across `MessageStart` boundaries to prevent duplicate rendering.
    ///
    /// # Arguments
    /// * `content_type` - The type of content
    /// * `index` - The content index (as string for flexibility)
    ///
    /// # Returns
    /// * `true` - This exact content has been rendered before
    /// * `false` - This exact content has not been rendered
    #[must_use]
    pub fn is_content_rendered(&self, content_type: ContentType, index: &str) -> bool {
        let content_key = (content_type, index.to_string());

        // Check if we have accumulated content for this key
        if let Some(current) = self.accumulated.get(&content_key) {
            let hash = Self::compute_hash(&current);

            // Check if this hash has been rendered before for this key
            return self
                .rendered_content_hashes
                .contains(&(content_type, index.to_string(), hash));
        }

        false
    }

    /// Check if content has been rendered before and starts with previously rendered content.
    ///
    /// This method detects when new content extends previously rendered content,
    /// indicating an in-place update should be performed (e.g., using carriage return).
    ///
    /// With the new KMP + Rolling Hash approach, this checks if output has started
    /// for this key, which indicates we're in an in-place update scenario.
    ///
    /// # Arguments
    /// * `content_type` - The type of content
    /// * `index` - The content index (as string for flexibility)
    ///
    /// # Returns
    /// * `true` - Output has started for this key (do in-place update)
    /// * `false` - Output has not started for this key (show new content)
    #[must_use]
    pub fn has_rendered_prefix(&self, content_type: ContentType, index: &str) -> bool {
        let content_key = (content_type, index.to_string());
        self.output_started_for_key.contains(&content_key)
    }

    /// Mark content as rendered using hash-based tracking.
    ///
    /// This method updates the `rendered_content_hashes` set to track all
    /// content that has been rendered for deduplication.
    ///
    /// # Arguments
    /// * `content_type` - The type of content
    /// * `index` - The content index (as string for flexibility)
    pub fn mark_content_rendered(&mut self, content_type: ContentType, index: &str) {
        // Also update last_rendered for compatibility
        self.mark_rendered(content_type, index);

        // Add the hash of the accumulated content to the rendered set
        let content_key = (content_type, index.to_string());
        if let Some(current) = self.accumulated.get(&content_key) {
            let hash = Self::compute_hash(&current);
            self.rendered_content_hashes
                .insert((content_type, index.to_string(), hash));
        }
    }

    /// Mark content as rendered using pre-sanitized content.
    ///
    /// This method uses the sanitized content (with whitespace normalized)
    /// for hash-based deduplication, which prevents duplicates when the
    /// accumulated content differs only by whitespace.
    ///
    /// # Arguments
    /// * `content_type` - The type of content
    /// * `index` - The content index (as string for flexibility)
    /// * `content` - The content to hash
    pub fn mark_content_hash_rendered(
        &mut self,
        content_type: ContentType,
        index: &str,
        content: &str,
    ) {
        // Also update last_rendered for compatibility
        self.mark_rendered(content_type, index);

        // Add the hash of the content to the rendered set.
        //
        // NOTE: We key by (content_type, index) so `clear_key()` can fully reset
        // per-substream deduplication.
        let hash = Self::compute_hash(&content);
        self.rendered_content_hashes
            .insert((content_type, index.to_string(), hash));
    }

    /// Check if sanitized content has already been rendered.
    ///
    /// This method checks the hash of the sanitized content against the
    /// rendered set to prevent duplicate rendering.
    ///
    /// # Arguments
    /// * `_content_type` - The type of content (kept for API consistency)
    /// * `_index` - The content index (kept for API consistency)
    /// * `sanitized_content` - The sanitized content to check
    ///
    /// # Returns
    /// * `true` - This exact content has been rendered before
    /// * `false` - This exact content has not been rendered
    #[must_use]
    pub fn is_content_hash_rendered(
        &self,
        content_type: ContentType,
        index: &str,
        content: &str,
    ) -> bool {
        let hash = Self::compute_hash(&content);

        // Check if this hash has been rendered before for this (content_type, index)
        self.rendered_content_hashes
            .contains(&(content_type, index.to_string(), hash))
    }
}
