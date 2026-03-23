//! Tests for streaming configuration and prefix debouncer.

use super::*;

#[test]
fn test_streaming_config_defaults() {
    let config = StreamingConfig::default();
    assert_eq!(config.prefix_delta_threshold, 0);
    assert!(config.prefix_time_threshold.is_none());
}

// Tests for PrefixDebouncer

#[test]
fn test_prefix_debouncer_default_first_only() {
    let mut debouncer = PrefixDebouncer::default();

    // First delta always shows prefix
    assert!(debouncer.should_show_prefix(true));

    // With default config (no thresholds), only first delta shows prefix
    // This preserves the original behavior
    assert!(!debouncer.should_show_prefix(false));
    assert!(!debouncer.should_show_prefix(false));
    assert!(!debouncer.should_show_prefix(false));
}

#[test]
fn test_prefix_debouncer_count_threshold() {
    let config = StreamingConfig {
        prefix_delta_threshold: 3,
        prefix_time_threshold: None,
    };
    let mut debouncer = PrefixDebouncer::new(config);

    // First delta always shows prefix
    assert!(debouncer.should_show_prefix(true));

    // Next 2 deltas should skip prefix
    assert!(!debouncer.should_show_prefix(false)); // delta 1
    assert!(!debouncer.should_show_prefix(false)); // delta 2

    // 3rd delta hits threshold, shows prefix
    assert!(debouncer.should_show_prefix(false)); // delta 3

    // Cycle resets
    assert!(!debouncer.should_show_prefix(false)); // delta 1
    assert!(!debouncer.should_show_prefix(false)); // delta 2
    assert!(debouncer.should_show_prefix(false)); // delta 3
}

#[test]
fn test_prefix_debouncer_reset() {
    let config = StreamingConfig {
        prefix_delta_threshold: 3,
        prefix_time_threshold: None,
    };
    let mut debouncer = PrefixDebouncer::new(config);

    // Build up delta count
    debouncer.should_show_prefix(true);
    debouncer.should_show_prefix(false);
    debouncer.should_show_prefix(false);

    // Reset clears state
    debouncer.reset();

    // After reset, next delta is treated as fresh
    // (but not "first delta" unless caller says so)
    assert!(!debouncer.should_show_prefix(false)); // delta 1 after reset
    assert!(!debouncer.should_show_prefix(false)); // delta 2
    assert!(debouncer.should_show_prefix(false)); // delta 3 hits threshold
}

#[test]
fn test_prefix_debouncer_first_delta_always_shows() {
    let config = StreamingConfig {
        prefix_delta_threshold: 100,
        prefix_time_threshold: None,
    };
    let mut debouncer = PrefixDebouncer::new(config);

    // First delta always shows prefix regardless of threshold
    assert!(debouncer.should_show_prefix(true));

    // Even after many skips, marking as first shows prefix
    for _ in 0..10 {
        debouncer.should_show_prefix(false);
    }
    assert!(debouncer.should_show_prefix(true)); // First delta again
}

#[test]
fn test_prefix_debouncer_time_threshold() {
    // Note: This test uses Duration::ZERO for immediate threshold.
    // In practice, time-based debouncing uses longer durations like 100ms.
    let config = StreamingConfig {
        prefix_delta_threshold: 0,
        prefix_time_threshold: Some(Duration::ZERO),
    };
    let mut debouncer = PrefixDebouncer::new(config);

    // First delta shows prefix
    assert!(debouncer.should_show_prefix(true));

    // Since threshold is ZERO, any elapsed time triggers prefix
    // In practice, Instant::now() moves forward, so this should show
    assert!(debouncer.should_show_prefix(false));
}
