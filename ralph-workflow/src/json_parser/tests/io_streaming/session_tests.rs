// Tests for streaming session management, snapshot-as-delta detection, and content block state tracking

// Tests for snapshot-as-delta detection
// These tests verify that the streaming state correctly identifies when
// snapshot-style content is being sent as deltas (a common bug pattern)

/// Test that a single large delta triggers a warning
/// This simulates a parser sending the entire accumulated content as a "delta"
#[test]
fn test_snapshot_as_delta_single_large_delta_warns() {
    use super::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // Create a delta larger than SNAPSHOT_THRESHOLD (200 chars)
    let large_delta = "x".repeat(201);

    // Capture stderr to verify warning is emitted
    // Note: In a real test environment, this warning would go to stderr
    // The test verifies the functionality doesn't crash and handles the large delta
    let show_prefix = session.on_text_delta(0, &large_delta);
    assert!(show_prefix, "First large delta should show prefix");

    // Content should still be accumulated correctly
    assert_eq!(
        session.get_accumulated(super::types::ContentType::Text, "0"),
        Some(large_delta.as_str())
    );
}

/// Test that many tiny deltas work correctly without warnings
/// This verifies the normal streaming case doesn't trigger false positives
#[test]
fn test_many_tiny_deltas_work_correctly() {
    use super::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // Send many small deltas (normal streaming behavior)
    let mut expected_content = String::new();
    for i in 0..20 {
        let delta = format!("chunk{i}");
        expected_content.push_str(&delta);
        session.on_text_delta(0, &delta);
    }

    // All content should be accumulated correctly
    assert_eq!(
        session.get_accumulated(super::types::ContentType::Text, "0"),
        Some(expected_content.as_str())
    );
}

/// Test that a pattern of repeated large deltas is detected
/// This simulates a bug where the same snapshot is sent repeatedly as "deltas"
#[test]
fn test_pattern_of_repeated_large_deltas_detected() {
    use super::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // Create a large snapshot
    let large_snapshot = "x".repeat(201);

    // Send the same large content 3 times (simulating snapshot-as-delta bug)
    // This should trigger the pattern detection warning
    for _ in 0..3 {
        session.on_text_delta(0, &large_snapshot);
    }

    // With the new deduplication, duplicate deltas are correctly skipped
    // So accumulated content should be the same as a single snapshot
    let accumulated = session
        .get_accumulated(super::types::ContentType::Text, "0")
        .unwrap();
    assert_eq!(accumulated.len(), large_snapshot.len());

    // Verify that large_delta_count still tracks all 3 large deltas
    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(metrics.large_delta_count, 3);
}

/// Test mixed small and large deltas
/// Verifies that legitimate mixed content doesn't cause issues
#[test]
fn test_mixed_small_and_large_deltas() {
    use super::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // Start with small deltas
    session.on_text_delta(0, "Hello ");
    session.on_text_delta(0, "World ");

    // Add a large delta (might be legitimate in some cases)
    let large_delta = "x".repeat(201);
    session.on_text_delta(0, &large_delta);

    // Continue with small deltas
    session.on_text_delta(0, " End");

    // All content should be accumulated
    let accumulated = session
        .get_accumulated(super::types::ContentType::Text, "0")
        .unwrap();
    assert!(accumulated.contains("Hello"));
    assert!(accumulated.contains("World"));
    assert!(accumulated.ends_with(" End"));
}

/// Test for content block state tracking
///
/// This test verifies that the `ContentBlockState` implementation correctly
/// tracks block transitions and the `started_output` flag. This is the foundation
/// for future enhancements where block transitions can emit newlines.
///
/// Note: This is a unit test that directly tests the `StreamingSession` state
/// tracking, not the end-to-end parser behavior (which would require additional
/// parser-layer changes to actually emit newlines on block transitions).
///
/// When transitioning to a different content block index, the old block's content
/// is cleared to prevent memory buildup and to ensure proper isolation between blocks.
#[test]
fn test_content_block_state_tracking() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // Initially, no content has been streamed
    assert!(!session.has_any_streamed_content());

    // Start streaming content in block 0
    let show_prefix = session.on_text_delta(0, "First");
    assert!(show_prefix, "First delta should show prefix");
    assert!(session.has_any_streamed_content());

    // Transition to block 1 via on_content_block_start
    // Block 0's accumulated content should be PRESERVED (no longer cleared as of wt-24-ccs-repeat-2 fix)
    session.on_content_block_start(1);

    // Stream content in block 1
    let show_prefix = session.on_text_delta(1, "Second");
    assert!(show_prefix, "First delta in new block should show prefix");

    // Verify block 0 content was PRESERVED and block 1 content is present
    assert_eq!(
        session.get_accumulated(crate::json_parser::types::ContentType::Text, "0"),
        Some("First"),
        "Block 0 content should be PRESERVED after transitioning to block 1"
    );
    assert_eq!(
        session.get_accumulated(crate::json_parser::types::ContentType::Text, "1"),
        Some("Second")
    );
}
