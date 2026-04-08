// Claude parser tests.

// These tests exercise monitoring/test-only APIs; they require the `test-utils` feature.
#[cfg(all(test, feature = "test-utils"))]
mod tool_activity_tracker_boundary_tests {
    use super::*;
    use std::sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    };

    /// Bug 3 regression: MessageStop must NOT clear tool_active.
    /// Claude Code executes the Write tool AFTER MessageStop; clearing at MessageStop
    /// causes the idle-timeout monitor to kill the agent during tool execution.
    /// Fix: clear tool_active at MessageStart (when tool result has been delivered).
    #[test]
    fn tool_active_not_cleared_at_message_stop_cleared_at_next_message_start() {
        let tracker = Arc::new(AtomicBool::new(false));
        let parser = ClaudeParser::new(Colors::new(), Verbosity::Normal)
            .with_tool_activity_tracker(Arc::clone(&tracker));

        // 1. ContentBlockStart with ToolUse — must set tracker to true.
        // Events are wrapped in ClaudeEvent::StreamEvent; the outer type is "stream_event".
        let cbs = concat!(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"#,
            r#""content_block":{"type":"tool_use","id":"toolu_01","name":"Write","input":{}}}}"#
        );
        let _ = parser.parse_event(cbs);
        assert!(
            tracker.load(Ordering::Acquire),
            "tool_active must be true after ContentBlockStart+ToolUse"
        );

        // 2. MessageStop: tool submitted but NOT yet executed — tracker must stay true
        let message_stop = r#"{"type":"stream_event","event":{"type":"message_stop"}}"#;
        let _ = parser.parse_event(message_stop);
        assert!(
            tracker.load(Ordering::Acquire),
            "tool_active must remain true after MessageStop; Write tool executes after this event"
        );

        // 3. MessageStart: tool result delivered — tracker must now be cleared
        let message_start = concat!(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_02","type":"message","#,
            r#""role":"assistant","content":[],"model":"claude-opus-4-6","#,
            r#""stop_reason":null,"stop_sequence":null,"#,
            r#""usage":{"input_tokens":10,"output_tokens":0}}}}"#
        );
        let _ = parser.parse_event(message_start);
        assert!(
            !tracker.load(Ordering::Acquire),
            "tool_active must be false after MessageStart — tool result was delivered"
        );
    }

    /// When no tool is in flight, MessageStop and MessageStart must leave tracker false.
    #[test]
    fn tracker_stays_false_when_no_tool_in_flight_across_message_boundary() {
        let tracker = Arc::new(AtomicBool::new(false));
        let parser = ClaudeParser::new(Colors::new(), Verbosity::Normal)
            .with_tool_activity_tracker(Arc::clone(&tracker));

        let message_stop = r#"{"type":"stream_event","event":{"type":"message_stop"}}"#;
        let _ = parser.parse_event(message_stop);
        assert!(
            !tracker.load(Ordering::Acquire),
            "tracker must stay false at MessageStop when no tool was in flight"
        );

        let message_start = concat!(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_02","type":"message","#,
            r#""role":"assistant","content":[],"model":"claude-opus-4-6","#,
            r#""stop_reason":null,"stop_sequence":null,"#,
            r#""usage":{"input_tokens":10,"output_tokens":0}}}}"#
        );
        let _ = parser.parse_event(message_start);
        assert!(
            !tracker.load(Ordering::Acquire),
            "tracker must stay false at MessageStart when no tool was in flight"
        );
    }
}

#[cfg(all(test, feature = "test-utils"))]
mod tests {
    use super::*;
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};

    #[test]
    fn test_printer_field_accessible() {
        // Test that the printer field is accessible and returns a SharedPrinter
        let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
        let parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, Rc::clone(&test_printer));

        // This test verifies the printer field is accessible
        let _printer_ref = &parser.printer;
    }

    #[test]
    fn test_show_streaming_metrics_builder() {
        // Test that the with_show_streaming_metrics builder method works
        let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
        let parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, Rc::clone(&test_printer))
                .with_show_streaming_metrics(true);

        // This test verifies the builder method is accessible
        assert!(parser.show_streaming_metrics);
    }
}
