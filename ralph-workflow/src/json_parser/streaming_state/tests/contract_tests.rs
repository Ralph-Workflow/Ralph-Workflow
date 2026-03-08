// Tests for delta contract validation.
//
// This file contains tests for:
// - Delta size validation and warnings
// - Streaming quality metrics
// - Environment variable configuration

#[test]
fn test_delta_validation_warns_on_large_delta() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Create a delta larger than snapshot_threshold()
    let large_delta = "x".repeat(snapshot_threshold() + 1);

    // This should trigger a warning but still work
    let show_prefix = session.on_text_delta(0, &large_delta);
    assert!(show_prefix);

    // Content should still be accumulated correctly
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some(large_delta.as_str())
    );
}

#[test]
fn test_delta_validation_no_warning_for_small_delta() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Small delta should not trigger warning
    let small_delta = "Hello, world!";
    let show_prefix = session.on_text_delta(0, small_delta);
    assert!(show_prefix);

    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some(small_delta)
    );
}

// Tests for enhanced streaming metrics

#[test]
fn test_streaming_quality_metrics_includes_snapshot_repairs() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta - long enough to meet threshold requirements
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta(0, initial);

    // GLM sends snapshot instead of delta (with strong overlap)
    let snapshot = format!("{initial} World!");
    let _ = session.on_text_delta(0, &snapshot);

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(
        metrics.snapshot_repairs_count, 1,
        "Should track one snapshot repair"
    );
}

#[test]
fn test_streaming_quality_metrics_includes_large_delta_count() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Send 3 large deltas
    for _ in 0..3 {
        let large_delta = "x".repeat(snapshot_threshold() + 1);
        let _ = session.on_text_delta(0, &large_delta);
    }

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(
        metrics.large_delta_count, 3,
        "Should track three large deltas"
    );
}

#[test]
fn test_streaming_quality_metrics_includes_protocol_violations() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_text_delta(0, "Hello");

    // Simulate GLM sending repeated MessageStart during streaming
    session.on_message_start();
    session.on_text_delta(0, " World");

    // Another violation
    session.on_message_start();

    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(
        metrics.protocol_violations, 2,
        "Should track two protocol violations"
    );
}

#[test]
fn test_streaming_quality_metrics_all_new_fields_zero_by_default() {
    let session = StreamingSession::new();
    let metrics = session.get_streaming_quality_metrics();

    assert_eq!(metrics.snapshot_repairs_count, 0);
    assert_eq!(metrics.large_delta_count, 0);
    assert_eq!(metrics.protocol_violations, 0);
}

#[test]
fn test_streaming_quality_metrics_comprehensive_tracking() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Normal delta
    session.on_text_delta(0, "Hello");

    // Large delta
    let large_delta = "x".repeat(snapshot_threshold() + 1);
    let _ = session.on_text_delta(0, &large_delta);

    // Snapshot repair (note: the snapshot is also large, so it counts as another large delta)
    let snapshot = format!("Hello{large_delta} World");
    let _ = session.on_text_delta(0, &snapshot);

    // Check metrics BEFORE the protocol violation (which clears delta_sizes)
    let metrics = session.get_streaming_quality_metrics();
    assert_eq!(metrics.snapshot_repairs_count, 1);
    assert_eq!(
        metrics.large_delta_count, 2,
        "Both the large delta and the snapshot are large"
    );
    assert_eq!(metrics.total_deltas, 3);
    assert_eq!(metrics.protocol_violations, 0, "No violation yet");

    // Protocol violation
    session.on_message_start();

    // After violation, protocol_violations is incremented but delta_sizes is cleared
    let metrics_after = session.get_streaming_quality_metrics();
    assert_eq!(metrics_after.protocol_violations, 1);
    assert_eq!(
        metrics_after.total_deltas, 0,
        "Delta sizes cleared after violation"
    );
}

// Tests for environment variable configuration

#[test]
fn test_snapshot_threshold_default() {
    let threshold = snapshot_threshold_from_env_fn(|_| None);
    assert_eq!(
        threshold, DEFAULT_SNAPSHOT_THRESHOLD,
        "Default threshold should be 200"
    );
}

#[test]
fn test_snapshot_threshold_parses_valid_override() {
    let threshold = snapshot_threshold_from_env_fn(|k| {
        (k == "RALPH_STREAMING_SNAPSHOT_THRESHOLD").then(|| "250".to_string())
    });
    assert_eq!(threshold, 250);
}

#[test]
fn test_snapshot_threshold_out_of_range_falls_back_to_default() {
    let threshold = snapshot_threshold_from_env_fn(|k| {
        (k == "RALPH_STREAMING_SNAPSHOT_THRESHOLD").then(|| "1".to_string())
    });
    assert_eq!(threshold, DEFAULT_SNAPSHOT_THRESHOLD);
}

#[test]
fn test_snapshot_threshold_invalid_value_falls_back_to_default() {
    let threshold = snapshot_threshold_from_env_fn(|k| {
        (k == "RALPH_STREAMING_SNAPSHOT_THRESHOLD").then(|| "not-a-number".to_string())
    });
    assert_eq!(threshold, DEFAULT_SNAPSHOT_THRESHOLD);
}
