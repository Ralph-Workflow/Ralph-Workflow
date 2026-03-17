impl StreamingSession {
    pub fn on_text_delta(&mut self, index: u64, delta: &str) -> bool {
        self.on_text_delta_key(&index.to_string(), delta)
    }

    /// Check for consecutive duplicate delta using the "3 strikes" heuristic.
    ///
    /// Detects resend glitches where the exact same delta arrives repeatedly.
    /// Returns true if the delta should be dropped (exceeded threshold), false otherwise.
    ///
    /// # Arguments
    /// * `content_key` - The content key to check
    /// * `delta` - The delta to check
    /// * `key_str` - The string key for logging
    ///
    /// # Returns
    /// * `true` - The delta should be dropped (consecutive duplicate exceeded threshold)
    /// * `false` - The delta should be processed
    fn check_consecutive_duplicate(
        &mut self,
        content_key: &(ContentType, String),
        delta: &str,
        key_str: &str,
    ) -> bool {
        let delta_hash = RollingHashWindow::compute_hash(delta);
        let thresholds = get_overlap_thresholds();

        if let Some((count, prev_hash)) = self.consecutive_duplicates.get_mut(content_key) {
            if *prev_hash == delta_hash {
                *count = count.saturating_add(1);
                // Check if we've exceeded the consecutive duplicate threshold
                if *count >= thresholds.consecutive_duplicate_threshold {
                    // This is a resend glitch - drop the delta entirely
                    if self.verbose_warnings {
                        let _ = writeln!(
                            std::io::stderr(),
                            "Warning: Dropping consecutive duplicate delta (count={count}, threshold={}). \
                            This appears to be a resend glitch. Key: '{key_str}', Delta: {delta:?}",
                            thresholds.consecutive_duplicate_threshold
                        );
                    }
                    // Don't update last_delta - preserve previous for comparison
                    return true;
                }
            } else {
                // Different delta - reset count and update hash
                *count = 1;
                *prev_hash = delta_hash;
            }
        } else {
            // First occurrence of this delta
            self.consecutive_duplicates
                .insert(content_key.clone(), (1, delta_hash));
        }

        false
    }

    /// Process a text delta with a string key and return whether prefix should be shown.
    ///
    /// This variant is for parsers that use string keys instead of numeric indices
    /// (e.g., Codex uses `agent_msg`, `reasoning`; Gemini uses `main`; `OpenCode` uses `main`).
    ///
    /// # Delta Validation
    ///
    /// This method validates that incoming content appears to be a genuine delta
    /// (small chunk) rather than a snapshot (full accumulated content). Large "deltas"
    /// that exceed `snapshot_threshold()` trigger a warning as they may indicate a
    /// contract violation.
    ///
    /// Additionally, we track patterns of delta sizes to detect repeated large
    /// content being sent as if it were incremental (a common snapshot-as-delta bug).
    ///
    /// # Arguments
    /// * `key` - The content key (e.g., `main`, `agent_msg`, `reasoning`)
    /// * `delta` - The text delta to accumulate
    ///
    /// # Returns
    /// * `true` - Show prefix with this delta (first chunk)
    /// * `false` - Don't show prefix (subsequent chunks)
    pub fn on_text_delta_key(&mut self, key: &str, delta: &str) -> bool {
        // Lifecycle enforcement: deltas should only arrive during streaming
        // or idle (first delta starts streaming), never after finalization
        self.assert_lifecycle_state(&[StreamingState::Idle, StreamingState::Streaming]);

        let content_key = (ContentType::Text, key.to_string());
        let delta_size = delta.len();

        // Track delta size and warn on large deltas BEFORE duplicate check
        // This ensures we track all received deltas even if they're duplicates
        if delta_size > snapshot_threshold() {
            self.large_delta_count = self.large_delta_count.saturating_add(1);
            if self.verbose_warnings {
                let _ = writeln!(
                    std::io::stderr(),
                    "Warning: Large delta ({delta_size} chars) for key '{key}'. \
                    This may indicate unusual streaming behavior or a snapshot being sent as a delta."
                );
            }
        }

        // Track delta size for pattern detection
        {
            let sizes = self.delta_sizes.entry(content_key.clone()).or_default();
            sizes.push(delta_size);

            // Keep only the most recent delta sizes
            if sizes.len() > self.max_delta_history {
                sizes.remove(0);
            }
        }

        // Check for exact duplicate delta (same delta sent twice)
        // This handles the ccs-glm repeated MessageStart scenario where the same
        // delta is sent multiple times. We skip processing exact duplicates ONLY when
        // the accumulated content is empty (indicating we just had a MessageStart and
        // this is a true duplicate, not just a repeated token in normal streaming).
        if let Some(last) = self.last_delta.get(&content_key) {
            if delta == last {
                // Check if accumulated content is empty (just after MessageStart)
                if let Some(current_accumulated) = self.accumulated.get(&content_key) {
                    // If accumulated content is empty, this is likely a ccs-glm duplicate
                    if current_accumulated.is_empty() {
                        // Skip without updating last_delta (to preserve previous delta for comparison)
                        return false;
                    }
                } else {
                    // No accumulated content yet, definitely after MessageStart
                    // Skip without updating last_delta
                    return false;
                }
            }
        }

        // Consecutive duplicate detection ("3 strikes" heuristic)
        // Detects resend glitches where the exact same delta arrives repeatedly.
        // This is different from the above check - it tracks HOW MANY TIMES
        // the same delta has arrived consecutively, not just if it matches once.
        if self.check_consecutive_duplicate(&content_key, delta, key) {
            return false;
        }

        // Auto-repair: Check if this is a snapshot being sent as a delta
        // Do this BEFORE any mutable borrows so we can use immutable methods.
        // Use content-based detection which is more reliable than size-based alone.
        let is_snapshot = self.is_likely_snapshot(delta, key);
        let actual_delta = if is_snapshot {
            // Extract only the new portion to prevent exponential duplication
            match self.get_delta_from_snapshot(delta, key) {
                Ok(extracted) => {
                    // Track successful snapshot repair
                    self.snapshot_repairs_count = self.snapshot_repairs_count.saturating_add(1);
                    extracted.to_string()
                }
                Err(e) => {
                    // Snapshot detection had a false positive - use the original delta
                    if self.verbose_warnings {
                        let _ = writeln!(
                            std::io::stderr(),
                            "Warning: Snapshot extraction failed: {e}. Using original delta."
                        );
                    }
                    delta.to_string()
                }
            }
        } else {
            // Genuine delta - use as-is
            delta.to_string()
        };

        // Pattern detection: Check if we're seeing repeated large deltas
        // This indicates the same content is being sent repeatedly (snapshot-as-delta)
        let sizes = self.delta_sizes.get(&content_key);
        if let Some(sizes) = sizes {
            if sizes.len() >= DEFAULT_PATTERN_DETECTION_MIN_DELTAS && self.verbose_warnings {
                // Check if at least 3 of the last N deltas were large
                let large_count = sizes.iter().filter(|&&s| s > snapshot_threshold()).count();
                if large_count >= DEFAULT_PATTERN_DETECTION_MIN_DELTAS {
                    let _ = writeln!(
                        std::io::stderr(),
                        "Warning: Detected pattern of {large_count} large deltas for key '{key}'. \
                        This strongly suggests a snapshot-as-delta bug where the same \
                        large content is being sent repeatedly. File: streaming_state.rs, Line: {}",
                        line!()
                    );
                }
            }
        }

        // If the actual delta is empty (identical content detected), skip processing
        if actual_delta.is_empty() {
            // Return false to indicate no prefix should be shown (content unchanged)
            return false;
        }

        // Mark that we're streaming text content
        self.streamed_types.insert(ContentType::Text, true);
        self.state = StreamingState::Streaming;

        // Update block state to track this block and mark output as started
        self.current_block = ContentBlockState::InBlock {
            index: key.to_string(),
            started_output: true,
        };

        // Check if this is the first delta for this key using output_started_for_key
        // This is independent of accumulated content to handle cases where accumulated
        // content may be cleared (e.g., repeated ContentBlockStart for same index)
        let is_first = !self.output_started_for_key.contains(&content_key);

        // Mark that output has started for this key
        self.output_started_for_key.insert(content_key.clone());

        // Accumulate the delta (using auto-repaired delta if snapshot was detected)
        self.accumulated
            .entry(content_key.clone())
            .and_modify(|buf| buf.push_str(&actual_delta))
            .or_insert_with(|| actual_delta);

        // Track the last delta for duplicate detection
        // Use the original delta for tracking (not the auto-repaired version)
        self.last_delta
            .insert(content_key.clone(), delta.to_string());

        // Track order
        if is_first {
            self.key_order.push(content_key);
        }

        // Show prefix only on the very first delta
        is_first
    }
}
