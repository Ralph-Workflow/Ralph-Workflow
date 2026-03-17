// State transition and management methods for StreamingSession.
//
// This file contains methods for managing streaming state transitions,
// message tracking, and content block lifecycle.

impl StreamingSession {
    /// Reset the session on new message start.
    ///
    /// This should be called when:
    /// - Claude: `MessageStart` event
    /// - Codex: `TurnStarted` event
    /// - Gemini: `init` event or new message
    /// - `OpenCode`: New part starts
    ///
    /// # Arguments
    /// * `message_id` - Optional unique identifier for this message (for deduplication)
    ///
    /// # Note on Repeated `MessageStart` Events
    ///
    /// Some agents (notably GLM/ccs-glm) send repeated `MessageStart` events during
    /// a single logical streaming session. When this happens while state is `Streaming`,
    /// we preserve `output_started_for_key` to prevent prefix spam on each delta that
    /// follows the repeated `MessageStart`. This is a defensive measure to handle
    /// non-standard agent protocols while maintaining correct behavior for legitimate
    /// multi-message scenarios.
    pub fn on_message_start(&mut self) {
        // Detect repeated MessageStart during active streaming
        let is_mid_stream_restart = self.state == StreamingState::Streaming;

        if is_mid_stream_restart {
            // Track protocol violation
            self.protocol_violations = self.protocol_violations.saturating_add(1);
            // Log the contract violation for debugging (only if verbose warnings enabled)
            if self.verbose_warnings {
                let _ = writeln!(
                    std::io::stderr(),
                    "Warning: Received MessageStart while state is Streaming. \
                    This indicates a non-standard agent protocol (e.g., GLM sending \
                    repeated MessageStart events). Preserving output_started_for_key \
                    to prevent prefix spam. File: state_management.rs, Line: {}",
                    line!()
                );
            }

            // Preserve output_started_for_key to prevent prefix spam.
            // std::mem::take replaces the HashSet with an empty one and returns the old values,
            // which we restore after clearing other state. This ensures repeated MessageStart
            // events don't reset output tracking, preventing duplicate prefix display.
            let preserved_output_started = std::mem::take(&mut self.output_started_for_key);

            // Also preserve last_delta to detect duplicate deltas across MessageStart boundaries
            let preserved_last_delta = std::mem::take(&mut self.last_delta);

            // Also preserve rendered_content_hashes to detect duplicate rendering across MessageStart
            let preserved_rendered_hashes = std::mem::take(&mut self.rendered_content_hashes);

            // Also preserve consecutive_duplicates to detect resend glitches across MessageStart
            let preserved_consecutive_duplicates = std::mem::take(&mut self.consecutive_duplicates);

            self.state = StreamingState::Idle;
            self.streamed_types.clear();
            self.current_block = ContentBlockState::NotInBlock;
            self.accumulated.clear();
            self.key_order.clear();
            self.delta_sizes.clear();
            self.last_rendered.clear();
            self.deduplicator.clear();
            self.tool_names.clear();

            // Restore preserved state
            self.output_started_for_key = preserved_output_started;
            self.last_delta = preserved_last_delta;
            self.rendered_content_hashes = preserved_rendered_hashes;
            self.consecutive_duplicates = preserved_consecutive_duplicates;
        } else {
            // Normal reset for new message
            self.state = StreamingState::Idle;
            self.streamed_types.clear();
            self.current_block = ContentBlockState::NotInBlock;
            self.accumulated.clear();
            self.key_order.clear();
            self.delta_sizes.clear();
            self.output_started_for_key.clear();
            self.last_rendered.clear();
            self.last_delta.clear();
            self.rendered_content_hashes.clear();
            self.consecutive_duplicates.clear();
            self.deduplicator.clear();
            self.tool_names.clear();
        }
        // Note: We don't reset current_message_id here - it's set by a separate method
        // This allows for more flexible message ID handling
    }

    /// Set the current message ID for tracking.
    ///
    /// This should be called when processing a `MessageStart` event that contains
    /// a message identifier. Used to prevent duplicate display of final messages.
    ///
    /// # Arguments
    /// * `message_id` - The unique identifier for this message (or None to clear)
    pub fn set_current_message_id(&mut self, message_id: Option<String>) {
        self.current_message_id = message_id;
    }

    /// Get the current message ID.
    ///
    /// # Returns
    /// * `Some(id)` - The current message ID
    /// * `None` - No message ID is set
    #[must_use]
    pub fn get_current_message_id(&self) -> Option<&str> {
        self.current_message_id.as_deref()
    }

    /// Check if a message ID represents a duplicate final message.
    ///
    /// This prevents displaying the same message twice - once after streaming
    /// completes and again when the final "Assistant" event arrives.
    ///
    /// # Arguments
    /// * `message_id` - The message ID to check
    ///
    /// # Returns
    /// * `true` - This message has already been displayed (is a duplicate)
    /// * `false` - This is a new message
    #[must_use]
    pub fn is_duplicate_final_message(&self, message_id: &str) -> bool {
        self.displayed_final_messages.contains(message_id)
    }

    /// Mark a message as displayed to prevent duplicate display.
    ///
    /// This should be called after displaying a message's final content.
    ///
    /// # Arguments
    /// * `message_id` - The message ID to mark as displayed
    pub fn mark_message_displayed(&mut self, message_id: &str) {
        self.displayed_final_messages.insert(message_id.to_string());
    }

    /// Mark that an assistant event has pre-rendered content BEFORE streaming started.
    ///
    /// This is used to handle the case where an assistant event arrives with full content
    /// BEFORE any streaming deltas. When this happens, we render the assistant event content
    /// and mark the `message_id` as pre-rendered. ALL subsequent streaming deltas for the
    /// same `message_id` should be suppressed to prevent duplication.
    ///
    /// # Arguments
    /// * `message_id` - The message ID that was pre-rendered
    pub fn mark_message_pre_rendered(&mut self, message_id: &str) {
        self.pre_rendered_message_ids.insert(message_id.to_string());
    }

    /// Check if a message was pre-rendered from an assistant event.
    ///
    /// This checks if the given `message_id` was previously rendered from an assistant
    /// event (before streaming started). If so, ALL subsequent streaming deltas for
    /// this message should be suppressed.
    ///
    /// # Arguments
    /// * `message_id` - The message ID to check
    ///
    /// # Returns
    /// * `true` - This message was pre-rendered, suppress all streaming output
    /// * `false` - This message was not pre-rendered, allow streaming output
    #[must_use]
    pub fn is_message_pre_rendered(&self, message_id: &str) -> bool {
        self.pre_rendered_message_ids.contains(message_id)
    }

    /// Check if assistant event content has already been rendered.
    ///
    /// This prevents duplicate assistant events with the same content from being rendered
    /// multiple times. GLM/CCS may send multiple assistant events during streaming with
    /// the same content but different `message_ids`.
    ///
    /// # Arguments
    /// * `content_hash` - The hash of the assistant event content
    ///
    /// # Returns
    /// * `true` - This content was already rendered, suppress rendering
    /// * `false` - This content was not rendered, allow rendering
    #[must_use]
    pub fn is_assistant_content_rendered(&self, content_hash: u64) -> bool {
        self.rendered_assistant_content_hashes
            .contains(&content_hash)
    }

    /// Mark assistant event content as having been rendered.
    ///
    /// This should be called after rendering an assistant event to prevent
    /// duplicate rendering of the same content.
    ///
    /// # Arguments
    /// * `content_hash` - The hash of the assistant event content that was rendered
    pub fn mark_assistant_content_rendered(&mut self, content_hash: u64) {
        self.rendered_assistant_content_hashes.insert(content_hash);
    }

    /// Mark the start of a content block.
    ///
    /// This should be called when:
    /// - Claude: `ContentBlockStart` event
    /// - Codex: `ItemStarted` with relevant type
    /// - Gemini: Content section begins
    /// - `OpenCode`: Part with content starts
    ///
    /// If we're already in a block, this method finalizes the previous block
    /// by emitting a newline if output had started.
    ///
    /// # Arguments
    /// * `index` - The content block index (for multi-block messages)
    pub fn on_content_block_start(&mut self, index: u64) {
        let index_str = index.to_string();

        // Note: Previous versions tracked whether we were transitioning to a different
        // index to selectively clear accumulated content. This is no longer needed since
        // we preserve all accumulated content until message_stop (see rationale below).

        // Finalize previous block if we're in one
        self.ensure_content_block_finalized();

        // DO NOT clear accumulated content when transitioning blocks.
        //
        // RATIONALE:
        // In non-TTY modes (Basic/None), per-delta output is suppressed and accumulated
        // content is flushed ONCE at message_stop for ALL blocks. Clearing accumulated
        // content when transitioning to a new block would lose earlier blocks' content,
        // causing only the LAST block to be output (Bug: CCS renderer repeats streamed
        // lines across deltas - wt-24-ccs-repeat-2).
        //
        // In Full TTY mode, accumulated content is unused (deltas rendered in-place), so
        // letting it persist until message_stop has negligible memory impact.
        //
        // Accumulated content is properly cleared at message_start for the next message.
        //
        // This fix ensures multi-block messages are correctly flushed in non-TTY modes:
        // - Message with blocks [0, 1, 2]: ALL blocks' content is preserved until
        //   message_stop, then flushed via accumulated_keys() iteration.
        // - No per-delta spam (suppression already implemented in renderers).
        // - Content from ALL blocks appears in final output.
        //
        // EVIDENCE from baseline testing (wt-24-ccs-repeat-2 continuation #1):
        // When accumulated content IS cleared on block transition (baseline behavior):
        // - test_ccs_glm_architecture_verification_none_mode FAILS: only tool input
        //   (c0...c99) present, thinking (t0...t99) and text (w0...w99) MISSING
        // - test_ccs_glm_interleaved_blocks_with_many_deltas_none_mode FAILS: thinking
        //   block 0 (t0_) MISSING, only later blocks appear
        // Root cause confirmed: Clearing accumulated content on block transition loses
        // earlier blocks, violating the suppress-accumulate-flush architecture.

        // Initialize the new content block
        self.current_block = ContentBlockState::InBlock {
            index: index_str,
            started_output: false,
        };
    }

    /// Ensure the current content block is finalized.
    ///
    /// If we're in a block and output has started, this returns true to indicate
    /// that a newline should be emitted. This prevents "glued text" bugs where
    /// content from different blocks is concatenated without separation.
    ///
    /// # Returns
    /// * `true` - A newline should be emitted (output had started)
    /// * `false` - No newline needed (no output or not in a block)
    fn ensure_content_block_finalized(&mut self) -> bool {
        if let ContentBlockState::InBlock { started_output, .. } = &self.current_block {
            let had_output = *started_output;
            self.current_block = ContentBlockState::NotInBlock;
            had_output
        } else {
            false
        }
    }

    /// Assert that the session is in a valid lifecycle state.
    ///
    /// In debug builds, this will panic if the current state doesn't match
    /// any of the expected states. In release builds, this does nothing.
    ///
    /// # Arguments
    /// * `expected` - Slice of acceptable states
    fn assert_lifecycle_state(&self, expected: &[StreamingState]) {
        #[cfg(debug_assertions)]
        assert!(
            expected.contains(&self.state),
            "Invalid lifecycle state: expected {:?}, got {:?}. \
            This indicates a bug in the parser's event handling.",
            expected,
            self.state
        );
        #[cfg(not(debug_assertions))]
        let _ = expected;
    }

    /// Finalize the message on stop event.
    ///
    /// This should be called when:
    /// - Claude: `MessageStop` event
    /// - Codex: `TurnCompleted` or `ItemCompleted` with text
    /// - Gemini: Message completion
    /// - `OpenCode`: Part completion
    ///
    /// # Returns
    /// * `true` - A completion newline should be emitted (was in a content block)
    /// * `false` - No completion needed (no content block active)
    pub fn on_message_stop(&mut self) -> bool {
        let was_in_block = self.ensure_content_block_finalized();
        self.state = StreamingState::Finalized;

        // Compute content hash for deduplication
        self.final_content_hash = self.compute_content_hash();

        // Mark the current message as displayed to prevent duplicate display
        // when the final "Assistant" event arrives
        if let Some(message_id) = self.current_message_id.clone() {
            self.mark_message_displayed(&message_id);
        }

        was_in_block
    }

    /// Clear all state for a specific (`content_type`, key) pair.
    ///
    /// This is used when a logical sub-stream completes (e.g., Codex `reasoning`
    /// item completion) but the overall turn/message continues.
    pub fn clear_key(&mut self, content_type: ContentType, key: &str) {
        let content_key = (content_type, key.to_string());
        self.accumulated.remove(&content_key);
        self.key_order.retain(|k| k != &content_key);
        self.output_started_for_key.remove(&content_key);
        self.delta_sizes.remove(&content_key);
        self.last_rendered.remove(&content_key);
        self.last_delta.remove(&content_key);
        self.consecutive_duplicates.remove(&content_key);

        // Clear any per-key rendered-hash entries so subsequent sub-streams reusing the
        // same key (e.g., Codex `reasoning`) won't be incorrectly suppressed as duplicates.
        self.rendered_content_hashes
            .retain(|(ct, k, _hash)| !(*ct == content_type && k == key));
    }

    /// Check if ANY content has been streamed for this message.
    ///
    /// This is a broader check that returns true if ANY content type
    /// has been streamed. Used to skip entire message display when
    /// all content was already streamed.
    #[must_use]
    pub fn has_any_streamed_content(&self) -> bool {
        !self.streamed_types.is_empty()
    }
}
