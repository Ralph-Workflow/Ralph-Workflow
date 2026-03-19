// Tests for format_unknown_json_event (shared utility) and event classification

#[test]
fn test_format_unknown_json_event_control_event() {
    let colors = Colors { enabled: false };
    // Control events should not show output even in verbose mode
    let json = r#"{"type":"message_start","message":{"id":"msg_123"}}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, true);
    // Control events should return empty string
    assert!(output.is_empty());
}

#[test]
fn test_format_unknown_json_event_partial_event() {
    let colors = Colors { enabled: false };
    // Partial events with content should show in non-verbose mode
    // The delta content should be extracted and shown directly
    let json =
        r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, false);
    // Should show content for partial events
    // Note: The delta text field is nested, so it will be extracted
    assert!(!output.is_empty());
}

#[test]
fn test_format_unknown_json_event_partial_event_verbose() {
    let colors = Colors { enabled: false };
    // Partial events should be labeled as such in verbose mode
    let json =
        r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, true);
    // Should show "Partial event:" in verbose mode
    assert!(!output.is_empty());
    assert!(output.contains("Partial event"));
}

#[test]
fn test_format_unknown_json_event_complete_event_verbose() {
    let colors = Colors { enabled: false };
    // Complete events only show in verbose mode
    let json = r#"{"type":"message","content":"This is a complete message with substantial content that should be displayed as is."}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, true);
    assert!(!output.is_empty());
    assert!(output.contains("Complete event"));
}

#[test]
fn test_format_unknown_json_event_complete_event_normal() {
    let colors = Colors { enabled: false };
    // Complete events should not show in non-verbose mode if no explicit content
    let json = r#"{"type":"status","status":"ok"}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, false);
    // Should return empty in non-verbose mode for complete events without content
    assert!(output.is_empty());
}

#[test]
fn test_format_unknown_json_event_with_explicit_delta_flag() {
    let colors = Colors { enabled: false };
    // Events with explicit delta: true should be detected as partial
    let json = r#"{"type":"message","delta":true,"content":"partial content"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, true);
    assert!(!output.is_empty());
    assert!(output.contains("Partial event"));
}

#[test]
fn test_format_unknown_json_event_error_control() {
    let colors = Colors { enabled: false };
    // Error events are control events and should not show
    let json = r#"{"type":"error","message":"Something went wrong"}"#;
    let output = super::types::format_unknown_json_event(json, "Claude", colors, true);
    // Error events are control events - should return empty
    assert!(output.is_empty());
}

#[test]
fn test_format_unknown_json_event_delta_shows_content_in_normal_mode() {
    let colors = Colors { enabled: false };
    // Delta events should show their content even in non-verbose mode
    let json = r#"{"type":"content_block_delta","delta":{"text":"Hello World"}}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should show the delta content in normal mode
    assert!(!output.is_empty());
    assert!(output.contains("Hello World"));
}

#[test]
fn test_format_unknown_json_event_partial_with_delta_field() {
    let colors = Colors { enabled: false };
    // Events with delta field should show content even without explicit type name
    let json = r#"{"type":"chunk","delta":"some partial content"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should show content because delta field is present
    assert!(!output.is_empty());
    assert!(output.contains("some partial content"));
}

#[test]
fn test_format_unknown_json_event_partial_with_text_field() {
    let colors = Colors { enabled: false };
    // Partial events with text field should show content
    let json = r#"{"type":"partial","text":"streaming text here"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should show content
    assert!(!output.is_empty());
    assert!(output.contains("streaming text here"));
}

#[test]
fn test_format_unknown_json_event_nested_delta_text() {
    let colors = Colors { enabled: false };
    // Nested delta.text should be extracted
    let json = r#"{"type":"update","delta":{"text":"nested content"}}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should show the nested delta text content
    assert!(!output.is_empty());
    assert!(output.contains("nested content"));
}

#[test]
fn test_format_unknown_json_event_deeply_nested_content() {
    let colors = Colors { enabled: false };
    // Test deeply nested content extraction
    // The current implementation extracts from delta.text or content fields
    // but not from arbitrary nested paths
    let json = r#"{"type":"delta","delta":{"text":"deep nested text"}}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should extract content from nested delta.text
    assert!(!output.is_empty());
    assert!(output.contains("deep nested text"));
}

#[test]
fn test_format_unknown_json_event_array_content() {
    let colors = Colors { enabled: false };
    // Test content extraction from arrays
    let json = r#"{"type":"message","content":["item1","item2","item3"]}"#;
    let _output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Arrays should be handled gracefully (content field exists but isn't a string)
    // The function should not crash
}

#[test]
fn test_format_unknown_json_event_empty_delta() {
    let colors = Colors { enabled: false };
    // Test delta with empty string content
    let json = r#"{"type":"content_block_delta","delta":{"text":""}}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Empty content should not show output
    assert!(output.is_empty() || output.trim().is_empty());
}

#[test]
fn test_format_unknown_json_event_whitespace_only_delta() {
    let colors = Colors { enabled: false };
    // Test delta with whitespace-only content
    let json = r#"{"type":"content_block_delta","delta":{"text":"   "}}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Whitespace-only content should not show output
    assert!(output.is_empty() || output.trim().is_empty());
}

#[test]
fn test_format_unknown_json_event_unicode_delta_content() {
    let colors = Colors { enabled: false };
    // Test Unicode content in delta
    let json = r#"{"type":"delta","text":"Hello 世界 🌍"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should show Unicode content properly
    assert!(!output.is_empty());
    assert!(output.contains("Hello 世界"));
}

#[test]
fn test_format_unknown_json_event_text_field_priority() {
    let colors = Colors { enabled: false };
    // Test that content field has priority over text field in classifier
    // The classifier's find_content_field returns "content" first (priority order)
    let json = r#"{"type":"content_delta","text":"first","content":"second"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should extract content since type contains "delta" (explicit partial)
    assert!(!output.is_empty());
    // The content field is prioritized by the classifier (not text)
    assert!(output.contains("second"));
}

#[test]
fn test_format_unknown_json_event_null_content_field() {
    let colors = Colors { enabled: false };
    // Test event with null content field
    let json = r#"{"type":"message","content":null}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Null content should be handled gracefully
    assert!(output.is_empty());
}

#[test]
fn test_format_unknown_json_event_numeric_content_field() {
    let colors = Colors { enabled: false };
    // Test event with numeric content (not a string)
    let json = r#"{"type":"metric","content":12345}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, true);
    // Numeric content should be shown in verbose mode
    assert!(!output.is_empty());
}

#[test]
fn test_format_unknown_json_event_boolean_delta_flag() {
    let colors = Colors { enabled: false };
    // Test explicit boolean delta flag
    let json = r#"{"type":"chunk","delta":true,"content":"test"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should detect delta: true and show content
    assert!(!output.is_empty());
    assert!(output.contains("test"));
}

#[test]
fn test_format_unknown_json_event_special_characters_in_content() {
    let colors = Colors { enabled: false };
    // Test special characters that might cause issues
    let json = r#"{"type":"delta","text":"Line1\nLine2\tTabbed\"Quoted"}"#;
    let output = super::types::format_unknown_json_event(json, "Test", colors, false);
    // Should handle special characters properly
    assert!(!output.is_empty());
}

#[test]
fn test_format_unknown_json_event_very_long_content() {
    let colors = Colors { enabled: false };
    // Test very long content doesn't cause issues
    let long_text = "a".repeat(10000);
    let json = format!(r#"{{"type":"delta","text":"{long_text}"}}"#);
    let output = super::types::format_unknown_json_event(&json, "Test", colors, false);
    // Should handle long content without crashing
    assert!(!output.is_empty());
    // Content should be shown in full for deltas (not truncated)
}

// Tests for stream classifier
#[test]
fn test_stream_classifies_short_content_as_partial() {
    use super::stream_classifier::{StreamEventClassifier, StreamEventType};
    let classifier = StreamEventClassifier::new();
    let event = serde_json::json!({
        "type": "chunk",
        "content": "Hi"
    });

    let result = classifier.classify(&event);
    assert_eq!(result.event_type, StreamEventType::Partial);
}

#[test]
fn test_stream_classifies_long_content_as_complete() {
    use super::stream_classifier::{StreamEventClassifier, StreamEventType};
    let classifier = StreamEventClassifier::new();
    let long_text = "This is a substantial message that exceeds the default threshold and should be considered complete.";
    let event = serde_json::json!({
        "type": "message",
        "content": long_text
    });

    let result = classifier.classify(&event);
    assert_eq!(result.event_type, StreamEventType::Complete);
}

#[test]
fn test_stream_classifies_status_without_content_as_control() {
    use super::stream_classifier::{StreamEventClassifier, StreamEventType};
    let classifier = StreamEventClassifier::new();
    let event = serde_json::json!({
        "type": "status",
        "status": "processing"
    });

    let result = classifier.classify(&event);
    assert_eq!(result.event_type, StreamEventType::Control);
}

#[cfg(test)]
#[test]
fn test_stream_classifies_error_as_control() {
    use super::stream_classifier::{StreamEventClassifier, StreamEventType};
    let classifier = StreamEventClassifier::new();
    let event = serde_json::json!({
        "type": "error",
        "message": "Something went wrong"
    });

    let result = classifier.classify(&event);
    assert_eq!(result.event_type, StreamEventType::Control);
}

// Tests for health monitoring
#[cfg(test)]
#[test]
fn test_claude_parser_tracks_partial_events_in_health_monitoring() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Create a stream with mixed events: control, partial (delta), and complete
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"message_stop"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Complete message"}]}}"#;

    let reader = Cursor::new(input);

    // Parse stream - should handle all events without health warnings
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(result.is_ok());

    // Verify output contains delta content
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();
    assert!(output.contains("Hello") || output.contains("World") || output.contains("Complete"));
}

#[test]
fn test_health_monitor_no_warning_with_high_partial_percentage() {
    use super::health::HealthMonitor;

    let monitor = HealthMonitor::new("test");
    let colors = Colors { enabled: false };

    // Simulate the bug report scenario: 97.5% partial events (2049 of 2102)
    // These should NOT trigger a warning because partial events are valid streaming content
    for _ in 0..2049 {
        monitor.record_partial_event();
    }
    for _ in 0..53 {
        monitor.record_parsed();
    }

    // Should NOT warn even with 97.5% "partial" events
    let warning = monitor.check_and_warn(colors);
    assert!(
        warning.is_none(),
        "Should not warn with high percentage of partial events"
    );
}

#[test]
fn test_health_monitor_warning_only_for_parse_errors() {
    use super::health::HealthMonitor;

    let monitor = HealthMonitor::new("test");
    let colors = Colors { enabled: false };

    // Mix of partial, control, and parsed events should NOT trigger warning
    for _ in 0..1000 {
        monitor.record_partial_event();
    }
    for _ in 0..500 {
        monitor.record_control_event();
    }
    for _ in 0..50 {
        monitor.record_parsed();
    }

    let warning = monitor.check_and_warn(colors);
    assert!(
        warning.is_none(),
        "Should not warn with mix of partial, control, and parsed events"
    );

    // Create a new monitor and test with actual parse errors
    let monitor = HealthMonitor::new("claude");

    // Add parse errors exceeding 50% threshold
    for _ in 0..60 {
        monitor.record_parse_error();
    }
    for _ in 0..40 {
        monitor.record_parsed();
    }

    let warning = monitor.check_and_warn(colors);
    assert!(warning.is_some(), "Should warn with >50% parse errors");
    assert!(warning.unwrap().contains("parse errors"));
}
