// Tests for StreamingSession lifecycle and content tracking.
//
// This file contains tests for:
// - Session lifecycle (start/stop/reset)
// - Accumulated content tracking
// - Multiple content indices
// - Token-by-token streaming
// - Message identity tracking
// - Repeated MessageStart handling (GLM protocol quirk)
// - Verbose warnings feature
// - Hash-based deduplication
// - Rapid index switching edge case (RFC-003)

#[test]
fn test_session_lifecycle() {
    let mut session = StreamingSession::new();

    // Initially no content streamed
    assert!(!session.has_any_streamed_content());

    // Message start
    session.on_message_start();
    assert!(!session.has_any_streamed_content());

    // Text delta
    let show_prefix = session.on_text_delta(0, "Hello");
    assert!(show_prefix);
    assert!(session.has_any_streamed_content());

    // Another delta
    let show_prefix = session.on_text_delta(0, " World");
    assert!(!show_prefix);

    // Message stop
    let was_in_block = session.on_message_stop();
    assert!(was_in_block);
}

#[test]
fn test_accumulated_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    session.on_text_delta(0, "Hello");
    session.on_text_delta(0, " World");

    let accumulated = session.get_accumulated(ContentType::Text, "0");
    assert_eq!(accumulated, Some("Hello World"));
}

#[test]
fn test_reset_between_messages() {
    let mut session = StreamingSession::new();

    // First message
    session.on_message_start();
    session.on_text_delta(0, "First");
    assert!(session.has_any_streamed_content());
    session.on_message_stop();

    // Second message - state should be reset
    session.on_message_start();
    assert!(!session.has_any_streamed_content());
}

#[test]
fn test_multiple_indices() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    session.on_text_delta(0, "First block");
    session.on_text_delta(1, "Second block");

    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("First block")
    );
    assert_eq!(
        session.get_accumulated(ContentType::Text, "1"),
        Some("Second block")
    );
}

#[test]
fn test_clear_index() {
    // Behavioral test: verify that creating a new session gives clean state
    // instead of testing clear_index() which is now removed
    let mut session = StreamingSession::new();
    session.on_message_start();

    session.on_text_delta(0, "Before");
    // Instead of clearing, verify that a new session starts fresh
    let mut fresh_session = StreamingSession::new();
    fresh_session.on_message_start();
    fresh_session.on_text_delta(0, "After");

    assert_eq!(
        fresh_session.get_accumulated(ContentType::Text, "0"),
        Some("After")
    );
    // Original session should still have "Before"
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("Before")
    );
}

#[test]
fn test_token_by_token_streaming_scenario() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Simulate token-by-token streaming
    let tokens = ["H", "e", "l", "l", "o", " ", "W", "o", "r", "l", "d", "!"];

    for token in tokens {
        let show_prefix = session.on_text_delta(0, token);

        // Only first token should show prefix
        if token == "H" {
            assert!(show_prefix, "First token should show prefix");
        } else {
            assert!(!show_prefix, "Subsequent tokens should not show prefix");
        }
    }

    // Verify accumulated content
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("Hello World!")
    );
}

// Tests for message identity tracking

#[test]
fn test_set_and_get_current_message_id() {
    let mut session = StreamingSession::new();

    // Initially no message ID
    assert!(session.get_current_message_id().is_none());

    // Set a message ID
    session.set_current_message_id(Some("msg-123".to_string()));
    assert_eq!(session.get_current_message_id(), Some("msg-123"));

    // Clear the message ID
    session.set_current_message_id(None);
    assert!(session.get_current_message_id().is_none());
}

#[test]
fn test_mark_message_displayed() {
    let mut session = StreamingSession::new();

    // Initially not marked as displayed
    assert!(!session.is_duplicate_final_message("msg-123"));

    // Mark as displayed
    session.mark_message_displayed("msg-123");
    assert!(session.is_duplicate_final_message("msg-123"));

    // Different message ID is not a duplicate
    assert!(!session.is_duplicate_final_message("msg-456"));
}

#[test]
fn test_message_stop_marks_displayed() {
    let mut session = StreamingSession::new();

    // Set a message ID
    session.set_current_message_id(Some("msg-123".to_string()));

    // Start a message with content
    session.on_message_start();
    session.on_text_delta(0, "Hello");

    // Stop should mark as displayed
    session.on_message_stop();
    assert!(session.is_duplicate_final_message("msg-123"));
}

#[test]
fn test_multiple_messages_tracking() {
    let mut session = StreamingSession::new();

    // First message
    session.set_current_message_id(Some("msg-1".to_string()));
    session.on_message_start();
    session.on_text_delta(0, "First");
    session.on_message_stop();
    assert!(session.is_duplicate_final_message("msg-1"));

    // Second message
    session.set_current_message_id(Some("msg-2".to_string()));
    session.on_message_start();
    session.on_text_delta(0, "Second");
    session.on_message_stop();
    assert!(session.is_duplicate_final_message("msg-1"));
    assert!(session.is_duplicate_final_message("msg-2"));
}

// Tests for repeated MessageStart handling (GLM/ccs-glm protocol quirk)

#[test]
fn test_repeated_message_start_preserves_output_started() {
    let mut session = StreamingSession::new();

    // First message start
    session.on_message_start();

    // First delta should show prefix
    let show_prefix = session.on_text_delta(0, "Hello");
    assert!(show_prefix, "First delta should show prefix");

    // Second delta should NOT show prefix
    let show_prefix = session.on_text_delta(0, " World");
    assert!(!show_prefix, "Second delta should not show prefix");

    // Simulate GLM sending repeated MessageStart during streaming
    // This should preserve output_started_for_key to prevent prefix spam
    session.on_message_start();

    // After repeated MessageStart, delta should NOT show prefix
    // because output_started_for_key was preserved
    let show_prefix = session.on_text_delta(0, "!");
    assert!(
        !show_prefix,
        "After repeated MessageStart, delta should not show prefix"
    );

    // Verify accumulated content was cleared (as expected for mid-stream restart)
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("!"),
        "Accumulated content should start fresh after repeated MessageStart"
    );
}

#[test]
fn test_repeated_message_start_with_normal_reset_between_messages() {
    let mut session = StreamingSession::new();

    // First message
    session.on_message_start();
    session.on_text_delta(0, "First");
    session.on_message_stop();

    // Second message - normal reset should clear output_started_for_key
    session.on_message_start();

    // First delta of second message SHOULD show prefix
    let show_prefix = session.on_text_delta(0, "Second");
    assert!(
        show_prefix,
        "First delta of new message should show prefix after normal reset"
    );
}

#[test]
fn test_repeated_message_start_with_multiple_indices() {
    let mut session = StreamingSession::new();

    // First message start
    session.on_message_start();

    // First delta for index 0
    let show_prefix = session.on_text_delta(0, "Index0");
    assert!(show_prefix, "First delta for index 0 should show prefix");

    // First delta for index 1
    let show_prefix = session.on_text_delta(1, "Index1");
    assert!(show_prefix, "First delta for index 1 should show prefix");

    // Simulate repeated MessageStart
    session.on_message_start();

    // After repeated MessageStart, deltas should NOT show prefix
    // because output_started_for_key was preserved for both indices
    let show_prefix = session.on_text_delta(0, " more");
    assert!(
        !show_prefix,
        "Delta for index 0 should not show prefix after repeated MessageStart"
    );

    let show_prefix = session.on_text_delta(1, " more");
    assert!(
        !show_prefix,
        "Delta for index 1 should not show prefix after repeated MessageStart"
    );
}

#[test]
fn test_repeated_message_start_during_thinking_stream() {
    let mut session = StreamingSession::new();

    // First message start
    session.on_message_start();

    // First thinking delta should show prefix
    let show_prefix = session.on_thinking_delta(0, "Thinking...");
    assert!(show_prefix, "First thinking delta should show prefix");

    // Simulate repeated MessageStart
    session.on_message_start();

    // After repeated MessageStart, thinking delta should NOT show prefix
    let show_prefix = session.on_thinking_delta(0, " more");
    assert!(
        !show_prefix,
        "Thinking delta after repeated MessageStart should not show prefix"
    );
}

#[test]
fn test_message_stop_then_message_start_resets_normally() {
    let mut session = StreamingSession::new();

    // First message
    session.on_message_start();
    session.on_text_delta(0, "First");

    // Message stop finalizes the message
    session.on_message_stop();

    // New message start should reset normally (not preserve output_started)
    session.on_message_start();

    // First delta of new message SHOULD show prefix
    let show_prefix = session.on_text_delta(0, "Second");
    assert!(
        show_prefix,
        "First delta after MessageStop should show prefix (normal reset)"
    );
}

#[test]
fn test_repeated_content_block_start_same_index() {
    let mut session = StreamingSession::new();

    // Message start
    session.on_message_start();

    // First delta for index 0
    let show_prefix = session.on_text_delta(0, "Hello");
    assert!(show_prefix, "First delta should show prefix");

    // Simulate repeated ContentBlockStart for same index
    // (Some agents send this, and we should NOT clear accumulated content)
    session.on_content_block_start(0);

    // Delta after repeated ContentBlockStart should NOT show prefix
    let show_prefix = session.on_text_delta(0, " World");
    assert!(
        !show_prefix,
        "Delta after repeated ContentBlockStart should not show prefix"
    );

    // Verify accumulated content was preserved
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("Hello World"),
        "Accumulated content should be preserved across repeated ContentBlockStart"
    );
}

// Tests for verbose_warnings feature

#[test]
fn test_large_delta_warning_respects_verbose_flag() {
    // Test with verbose warnings enabled
    let mut session_verbose = StreamingSession::new().with_verbose_warnings(true);
    session_verbose.on_message_start();

    let large_delta = "x".repeat(snapshot_threshold() + 1);
    // This would emit a warning to stderr if verbose_warnings is enabled
    let _show_prefix = session_verbose.on_text_delta(0, &large_delta);

    // Test with verbose warnings disabled (default)
    let mut session_quiet = StreamingSession::new();
    session_quiet.on_message_start();

    let large_delta = "x".repeat(snapshot_threshold() + 1);
    // This should NOT emit a warning
    let _show_prefix = session_quiet.on_text_delta(0, &large_delta);

    // Both sessions should accumulate content correctly
    assert_eq!(
        session_verbose.get_accumulated(ContentType::Text, "0"),
        Some(large_delta.as_str())
    );
    assert_eq!(
        session_quiet.get_accumulated(ContentType::Text, "0"),
        Some(large_delta.as_str())
    );
}

#[test]
fn test_repeated_message_start_warning_respects_verbose_flag() {
    // Test with verbose warnings enabled
    let mut session_verbose = StreamingSession::new().with_verbose_warnings(true);
    session_verbose.on_message_start();
    session_verbose.on_text_delta(0, "Hello");
    // This would emit a warning about repeated MessageStart
    session_verbose.on_message_start();

    // Test with verbose warnings disabled (default)
    let mut session_quiet = StreamingSession::new();
    session_quiet.on_message_start();
    session_quiet.on_text_delta(0, "Hello");
    // This should NOT emit a warning
    session_quiet.on_message_start();

    // Both sessions should handle the restart correctly
    assert_eq!(
        session_verbose.get_accumulated(ContentType::Text, "0"),
        None,
        "Accumulated content should be cleared after repeated MessageStart"
    );
    assert_eq!(
        session_quiet.get_accumulated(ContentType::Text, "0"),
        None,
        "Accumulated content should be cleared after repeated MessageStart"
    );
}

#[test]
fn test_pattern_detection_warning_respects_verbose_flag() {
    // Test with verbose warnings enabled
    let mut session_verbose = StreamingSession::new().with_verbose_warnings(true);
    session_verbose.on_message_start();

    // Send 3 large deltas to trigger pattern detection
    // Use different content to avoid consecutive duplicate detection
    for i in 0..3 {
        let large_delta = format!("{}{i}", "x".repeat(snapshot_threshold() + 1));
        let _ = session_verbose.on_text_delta(0, &large_delta);
    }

    // Test with verbose warnings disabled (default)
    let mut session_quiet = StreamingSession::new();
    session_quiet.on_message_start();

    // Send 3 large deltas (different content to avoid consecutive duplicate detection)
    for i in 0..3 {
        let large_delta = format!("{}{i}", "x".repeat(snapshot_threshold() + 1));
        let _ = session_quiet.on_text_delta(0, &large_delta);
    }

    // Verify that large_delta_count still tracks all 3 large deltas for both sessions
    assert_eq!(
        session_verbose
            .get_streaming_quality_metrics()
            .large_delta_count,
        3
    );
    assert_eq!(
        session_quiet
            .get_streaming_quality_metrics()
            .large_delta_count,
        3
    );
}

// Tests for hash-based deduplication

#[test]
fn test_content_hash_computed_on_message_stop() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "Hello");
    session.on_text_delta(0, " World");

    // Hash should be recognized after message_stop
    session.on_message_stop();
    assert!(
        session.is_duplicate_by_hash("Hello World", None),
        "Hash should be computed after message_stop"
    );
}

#[test]
fn test_content_hash_none_when_no_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // No content streamed
    session.on_message_stop();
    assert!(
        !session.is_duplicate_by_hash("any content", None),
        "No hash when no content was streamed"
    );
}

#[test]
fn test_is_duplicate_by_hash_returns_true_for_matching_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "Hello World");
    session.on_message_stop();

    // Same content should be detected as duplicate
    assert!(session.is_duplicate_by_hash("Hello World", None));
}

#[test]
fn test_is_duplicate_by_hash_returns_false_for_different_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "Hello World");
    session.on_message_stop();

    // Different content should NOT be detected as duplicate
    assert!(!session.is_duplicate_by_hash("Different content", None));
}

#[test]
fn test_is_duplicate_by_hash_returns_false_when_no_content_streamed() {
    let session = StreamingSession::new();

    // No content streamed, so no hash
    assert!(!session.is_duplicate_by_hash("Hello World", None));
}

#[test]
fn test_content_hash_multiple_content_blocks() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "First block");
    session.on_text_delta(1, "Second block");
    session.on_message_stop();

    // Individual content shouldn't match the combined content
    assert!(!session.is_duplicate_by_hash("First block", None));
    assert!(!session.is_duplicate_by_hash("Second block", None));
}

#[test]
fn test_is_duplicate_by_hash_returns_true_for_matching_mixed_text_and_tool_use_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "Let's inspect this ");
    session.on_tool_input_delta(1, "{\"path\":\"src\"}");
    session.set_tool_name(1, Some("read_file".to_string()));
    session.on_message_stop();

    let normalized_content = "Let's inspect this TOOL_USE:read_file:{\"path\":\"src\"}";

    assert!(session.is_duplicate_by_hash(normalized_content, None));
}

#[test]
fn test_content_hash_consistent_for_same_content() {
    let mut session1 = StreamingSession::new();
    session1.on_message_start();
    session1.on_text_delta(0, "Hello");
    session1.on_text_delta(0, " World");
    session1.on_message_stop();

    let mut session2 = StreamingSession::new();
    session2.on_message_start();
    session2.on_text_delta(0, "Hello World");
    session2.on_message_stop();

    // Same content should be recognized as duplicate in both sessions
    assert!(session1.is_duplicate_by_hash("Hello World", None));
    assert!(session2.is_duplicate_by_hash("Hello World", None));
}

#[test]
fn test_content_hash_multiple_content_blocks_non_sequential_indices() {
    // Test that content hash uses numeric sorting, not lexicographic sorting.
    // This prevents bugs when GLM sends non-sequential indices like 0, 1, 10, 2.
    // With lexicographic sorting, indices would be ordered as 0, 1, 10, 2,
    // but with numeric sorting they should be ordered as 0, 1, 2, 10.
    let mut session1 = StreamingSession::new();
    session1.on_message_start();
    // Add content in non-sequential order: 0, 1, 10, 2
    session1.on_text_delta(0, "Block 0");
    session1.on_text_delta(1, "Block 1");
    session1.on_text_delta(10, "Block 10");
    session1.on_text_delta(2, "Block 2");
    session1.on_message_stop();

    let mut session2 = StreamingSession::new();
    session2.on_message_start();
    // Add the same content but in numeric order: 0, 1, 2, 10
    session2.on_text_delta(0, "Block 0");
    session2.on_text_delta(1, "Block 1");
    session2.on_text_delta(2, "Block 2");
    session2.on_text_delta(10, "Block 10");
    session2.on_message_stop();

    // Verify that is_duplicate_by_hash works correctly for both sessions
    // (both should recognize the combined content in numeric order as a match)
    let combined_content = "Block 0Block 1Block 2Block 10";
    assert!(
        session1.is_duplicate_by_hash(combined_content, None),
        "is_duplicate_by_hash should match content in numeric order"
    );
    assert!(
        session2.is_duplicate_by_hash(combined_content, None),
        "session2 hash should also match content in numeric order"
    );
}

// Tests for rapid index switching edge case (RFC-003)

#[test]
fn test_rapid_index_switch_with_clear() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Start block 0 and accumulate content
    session.on_content_block_start(0);
    let show_prefix = session.on_text_delta(0, "X");
    assert!(show_prefix, "First delta for index 0 should show prefix");
    assert_eq!(session.get_accumulated(ContentType::Text, "0"), Some("X"));

    // Switch to block 1 - accumulated content for index 0 should be PRESERVED
    // (no longer cleared on index switch as of wt-24-ccs-repeat-2 fix)
    session.on_content_block_start(1);

    // Verify accumulated for index 0 was PRESERVED
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("X"),
        "Accumulated content for index 0 should be PRESERVED when switching to index 1"
    );

    // Switch back to index 0
    session.on_content_block_start(0);

    // Since output_started_for_key was preserved, prefix should NOT show again
    let show_prefix = session.on_text_delta(0, "Y");
    assert!(
        !show_prefix,
        "Prefix should NOT show when switching back to an index that already started output"
    );

    // Verify content is APPENDED to existing accumulated content
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some("XY"),
        "New content should be APPENDED to preserved accumulated content"
    );
}

#[test]
fn test_delta_sizes_preserved_on_index_switch() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Track some deltas for index 0
    session.on_text_delta(0, "Hello");
    session.on_text_delta(0, " World");

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(metrics.total_deltas, 2, "Two deltas tracked for index 0");

    // Switch to index 1 - delta_sizes for index 0 should be PRESERVED
    // (needed for non-TTY flush at message_stop)
    session.on_content_block_start(1);

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(
        metrics.total_deltas, 2,
        "Delta sizes for index 0 preserved when switching to index 1"
    );

    // Add a delta for index 1 - total should grow, proving index 0 was preserved
    // and index 1 started fresh (if index 0 were cleared, total would only be 1)
    session.on_text_delta(1, "New");

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(
        metrics.total_deltas, 3,
        "Total deltas includes both index 0 (2) and index 1 (1) entries"
    );
}

#[test]
fn test_rapid_index_switch_with_thinking_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Start thinking content in index 0
    session.on_content_block_start(0);
    let show_prefix = session.on_thinking_delta(0, "Thinking...");
    assert!(show_prefix, "First thinking delta should show prefix");
    assert_eq!(
        session.get_accumulated(ContentType::Thinking, "0"),
        Some("Thinking...")
    );

    // Switch to text content in index 1 - index 0's accumulated should be PRESERVED
    // (no longer cleared on index switch as of wt-24-ccs-repeat-2 fix)
    session.on_content_block_start(1);

    // Verify index 0's accumulated thinking was PRESERVED
    assert_eq!(
        session.get_accumulated(ContentType::Thinking, "0"),
        Some("Thinking..."),
        "Thinking content for index 0 should be PRESERVED when switching to index 1"
    );

    let show_prefix = session.on_text_delta(1, "Text");
    assert!(
        show_prefix,
        "First text delta for index 1 should show prefix"
    );

    // Switch back to index 0 for thinking
    session.on_content_block_start(0);

    // Since output_started_for_key for (Thinking, "0") was PRESERVED,
    // the prefix should NOT show again
    let show_prefix = session.on_thinking_delta(0, " more");
    assert!(
        !show_prefix,
        "Thinking prefix should NOT show when switching back to an index that already started output"
    );

    // Verify thinking content was APPENDED to existing accumulated content
    assert_eq!(
        session.get_accumulated(ContentType::Thinking, "0"),
        Some("Thinking... more"),
        "Thinking content should be APPENDED to preserved accumulated content"
    );
}

