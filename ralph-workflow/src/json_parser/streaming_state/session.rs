// Streaming session tracker implementation.
//
// This file contains the `StreamingSession` struct and all its implementation
// methods for tracking streaming state across all parsers.
//
// FLATENED: All content from session/ subdirectory has been inlined into this file.
// This module was restructured to ensure all session logic is in a single file
// for better code organization and simpler module structure.

use crate::json_parser::deduplication::get_overlap_thresholds;
use crate::json_parser::deduplication::rolling_hash::RollingHashWindow;
use std::io::Write as IoWrite;

// ============================================================================
// StreamingSession struct definition
// ============================================================================

/// Unified streaming session tracker.
///
/// Provides a single source of truth for streaming state across all parsers.
/// Tracks:
/// - Current streaming state (`Idle`/`Streaming`/`Finalized`)
/// - Which content types have been streamed
/// - Accumulated content by content type and index
/// - Whether prefix should be shown on next delta
/// - Delta size patterns for detecting snapshot-as-delta violations
/// - Persistent "output started" tracking independent of accumulated content
/// - Verbosity-aware warning emission
///
/// # Lifecycle
///
/// 1. **Start**: `on_message_start()` - resets all state
/// 2. **Stream**: `on_text_delta()` / `on_thinking_delta()` - accumulate content
/// 3. **Stop**: `on_message_stop()` - finalize the message
/// 4. **Repeat**: Back to step 1 for next message
#[derive(Debug, Default, Clone)]
pub struct StreamingSession {
    /// Current streaming state
    pub(super) state: StreamingState,
    /// Track which content types have been streamed (for deduplication)
    /// Maps `ContentType` → whether it has been streamed
    pub(super) streamed_types: HashMap<ContentType, bool>,
    /// Track the current content block state
    pub(super) current_block: ContentBlockState,
    /// Accumulated content by (`content_type`, index) for display
    /// This mirrors `DeltaAccumulator` but adds deduplication tracking
    pub(super) accumulated: HashMap<(ContentType, String), String>,
    /// Track the order of keys for `most_recent` operations
    pub(super) key_order: Vec<(ContentType, String)>,
    /// Track recent delta sizes for pattern detection
    /// Maps `(content_type, key)` → vec of recent delta sizes
    pub(super) delta_sizes: HashMap<(ContentType, String), Vec<usize>>,
    /// Maximum number of delta sizes to track per key
    pub(super) max_delta_history: usize,
    /// Track the current message ID for duplicate detection
    pub(super) current_message_id: Option<String>,
    /// Track which messages have been displayed to prevent duplicate final output
    pub(super) displayed_final_messages: HashSet<String>,
    /// Track which (`content_type`, key) pairs have had output started.
    /// This is independent of `accumulated` to handle cases where accumulated
    /// content may be cleared (e.g., repeated `ContentBlockStart` for same index).
    /// Cleared on `on_message_start` to ensure fresh state for each message.
    pub(super) output_started_for_key: HashSet<(ContentType, String)>,
    /// Whether to emit verbose warnings about streaming anomalies.
    /// When false, suppresses diagnostic warnings that are useful for debugging
    /// but noisy in production (e.g., GLM protocol violations, snapshot detection).
    pub(super) verbose_warnings: bool,
    /// Count of snapshot repairs performed during this session
    pub(super) snapshot_repairs_count: usize,
    /// Count of deltas that exceeded the size threshold
    pub(super) large_delta_count: usize,
    /// Count of protocol violations detected (e.g., `MessageStart` during streaming)
    pub(super) protocol_violations: usize,
    /// Hash of the final streamed content (for deduplication)
    /// Computed at `message_stop` using all accumulated content
    pub(super) final_content_hash: Option<u64>,
    /// Track the last rendered content for each key to detect when rendering
    /// would produce identical output (prevents visual repetition).
    /// Maps `(content_type, key)` → the last accumulated content that was rendered.
    pub(super) last_rendered: HashMap<(ContentType, String), String>,
    /// Track rendered content hashes for duplicate detection.
    ///
    /// This stores a hash of the *sanitized content* together with the `(content_type, key)`
    /// it was rendered for. This is preserved across repeated `MessageStart` boundaries.
    ///
    /// Keying by `(content_type, key)` is important because some parsers reuse keys within the
    /// same turn (e.g., Codex can reuse `reasoning` for multiple items). When that happens,
    /// we need `clear_key()` to fully reset per-key deduplication state.
    pub(super) rendered_content_hashes: HashSet<(ContentType, String, u64)>,
    /// Track the last delta for each key to detect exact duplicate deltas.
    /// This is preserved across `MessageStart` boundaries to prevent duplicate processing.
    /// Maps `(content_type, key)` → the last delta that was processed.
    pub(super) last_delta: HashMap<(ContentType, String), String>,
    /// Track consecutive duplicates for resend glitch detection ("3 strikes" heuristic).
    /// Maps `(content_type, key)` → (count, `delta_hash`) where count tracks how many
    /// times the exact same delta has arrived consecutively. When count exceeds
    /// the threshold, the delta is dropped as a resend glitch.
    pub(super) consecutive_duplicates: HashMap<(ContentType, String), (usize, u64)>,
    /// Delta deduplicator using KMP and rolling hash for snapshot detection.
    /// Provides O(n+m) guaranteed complexity for detecting snapshot-as-delta violations.
    /// Cleared on message boundaries to prevent false positives.
    pub(super) deduplicator: DeltaDeduplicator,
    /// Track message IDs that have been fully rendered from an assistant event BEFORE streaming.
    /// When an assistant event arrives before streaming deltas, we render it and record
    /// the `message_id`. ALL subsequent streaming deltas for that `message_id` should be
    /// suppressed to prevent duplication.
    pub(super) pre_rendered_message_ids: HashSet<String>,
    /// Track content hashes of assistant events that have been rendered during streaming.
    /// This prevents duplicate assistant events with the same content from being rendered
    /// multiple times. GLM/CCS may send multiple assistant events during streaming with
    /// the same content but different `message_ids`.
    /// This is preserved across `MessageStart` boundaries to handle mid-stream assistant events.
    pub(super) rendered_assistant_content_hashes: HashSet<u64>,
    /// Track tool names by index for GLM/CCS deduplication.
    /// GLM sends assistant events with `tool_use` blocks (name + input) during streaming,
    /// but only the input is accumulated via deltas. We track the tool name to properly
    /// reconstruct the normalized representation for deduplication.
    /// Maps the content block index to the tool name.
    pub(super) tool_names: HashMap<u64, Option<String>>,
}

// ============================================================================
// StreamingSession impl block: constructors and basic methods
// ============================================================================

impl StreamingSession {
    /// Create a new streaming session.
    #[must_use]
    pub fn new() -> Self {
        Self {
            max_delta_history: DEFAULT_MAX_DELTA_HISTORY,
            verbose_warnings: false,
            ..Default::default()
        }
    }

    /// Configure whether to emit verbose warnings about streaming anomalies.
    ///
    /// When enabled, diagnostic warnings are printed for:
    /// - Repeated `MessageStart` events (GLM protocol violations)
    /// - Large deltas that may indicate snapshot-as-delta bugs
    /// - Pattern detection of repeated large content
    ///
    /// When disabled (default), these warnings are suppressed to avoid
    /// noise in production output.
    ///
    /// # Arguments
    /// * `enabled` - Whether to enable verbose warnings
    ///
    /// # Returns
    /// The modified session for builder chaining.
    ///
    /// # Example
    ///
    /// ```ignore
    /// let mut session = StreamingSession::new().with_verbose_warnings(true);
    /// ```
    #[must_use]
    pub const fn with_verbose_warnings(mut self, enabled: bool) -> Self {
        self.verbose_warnings = enabled;
        self
    }
}

// ============================================================================
// StreamingSession impl block: state management methods
// ============================================================================

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

// ============================================================================
// StreamingSession impl block: text delta handling
// ============================================================================

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

// ============================================================================
// StreamingSession impl block: thinking delta handling
// ============================================================================

impl StreamingSession {
    pub fn on_thinking_delta(&mut self, index: u64, delta: &str) -> bool {
        self.on_thinking_delta_key(&index.to_string(), delta)
    }

    /// Process a thinking delta with a string key and return whether prefix should be shown.
    ///
    /// This variant is for parsers that use string keys instead of numeric indices.
    ///
    /// # Arguments
    /// * `key` - The content key (e.g., "reasoning")
    /// * `delta` - The thinking delta to accumulate
    ///
    /// # Returns
    /// * `true` - Show prefix with this delta (first chunk)
    /// * `false` - Don't show prefix (subsequent chunks)
    pub fn on_thinking_delta_key(&mut self, key: &str, delta: &str) -> bool {
        // Mark that we're streaming thinking content
        self.streamed_types.insert(ContentType::Thinking, true);
        self.state = StreamingState::Streaming;

        merge_delta(
            &mut self.accumulated,
            &mut self.key_order,
            &mut self.output_started_for_key,
            ContentType::Thinking,
            key,
            delta,
        )
    }
}

// ============================================================================
// StreamingSession impl block: tool input delta handling
// ============================================================================

impl StreamingSession {
    pub fn on_tool_input_delta(&mut self, index: u64, delta: &str) {
        // Mark that we're streaming tool input
        self.streamed_types.insert(ContentType::ToolInput, true);
        self.state = StreamingState::Streaming;

        let _ = merge_delta(
            &mut self.accumulated,
            &mut self.key_order,
            &mut self.output_started_for_key,
            ContentType::ToolInput,
            &index.to_string(),
            delta,
        );
    }

    /// Record the tool name for a specific content block index.
    ///
    /// This is used for GLM/CCS deduplication where assistant events contain
    /// `tool_use` blocks (name + input) but streaming only accumulates the input.
    /// By tracking the name separately, we can reconstruct the normalized
    /// representation for proper hash-based deduplication.
    ///
    /// # Arguments
    /// * `index` - The content block index
    /// * `name` - The tool name (if available)
    pub fn set_tool_name(&mut self, index: u64, name: Option<String>) {
        self.tool_names.insert(index, name);
    }
}

// ============================================================================
// StreamingSession impl block: rendering and accumulation tracking
// ============================================================================

impl StreamingSession {
    pub fn get_accumulated(&self, content_type: ContentType, index: &str) -> Option<&str> {
        self.accumulated
            .get(&(content_type, index.to_string()))
            .map(std::string::String::as_str)
    }

    /// Return the set of accumulated keys for a given content type.
    ///
    /// This is used by non-TTY flush logic to render the final accumulated content
    /// once at a completion boundary (e.g., `message_stop`) without relying on
    /// arbitrary index bounds.
    #[must_use]
    pub fn accumulated_keys(&self, content_type: ContentType) -> Vec<String> {
        sorted_content_keys(&self.accumulated, content_type)
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
            let hash = compute_hash(current);

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
            let hash = compute_hash(current);
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
        let hash = compute_hash(content);
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
        let hash = compute_hash(content);

        // Check if this hash has been rendered before for this (content_type, index)
        self.rendered_content_hashes
            .contains(&(content_type, index.to_string(), hash))
    }
}
