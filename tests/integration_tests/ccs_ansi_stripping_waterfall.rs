//! Test that streaming works correctly in ANSI-stripping consoles.
//!
//! Bug: Full mode pattern uses `\n\x1b[1A` - if ANSI is stripped, the `\n` remains
//! but cursor positioning is ignored, creating multi-line spam.
//!
//! Expected: Append-only streaming produces no per-delta newlines, avoiding spam.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::claude::ClaudeParser;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::strip_ansi_codes;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::BufReader;
use std::rc::Rc;

#[test]
fn test_ansi_stripping_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Stream multiple deltas
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let stripped = strip_ansi_codes(&output);

        // Count newlines in stripped output
        let newline_count = stripped.matches('\n').count();

        // With append-only streaming, we expect:
        // - 0 newlines during streaming (no per-delta newlines)
        // - 1 newline at completion
        // With buggy pattern (\n\x1b[1A per delta), stripping ANSI leaves:
        // - 3 newlines during streaming (one per delta)
        // - 1 newline at completion = 4 total

        assert!(
            newline_count <= 1,
            "Expected <= 1 newline in ANSI-stripped output, found {newline_count}. \
             This indicates the Full mode pattern emits per-delta newlines that become \
             visible when ANSI cursor control is stripped.\n\
             Stripped output:\n{stripped}"
        );

        // Verify content is present
        assert!(
            stripped.contains("Hello World!"),
            "Content should be present. Stripped output:\n{stripped}"
        );
    });
}
