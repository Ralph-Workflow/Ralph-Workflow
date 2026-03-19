//! Tests for `OpenCode` JSON parser.

use super::*;
use crate::config::Verbosity;
use crate::logger::Colors;
use crate::workspace::MemoryWorkspace;
use std::io::Cursor;

#[test]
fn test_parse_opencode_tool_output_object_payload() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Verbose);
    let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/test.rs"},"output":{"ok":true,"bytes":123}}}}"#;
    let output = parser.parse_event(json).unwrap();
    assert!(output.contains("Output"));
    assert!(output.contains("ok"));
}

#[test]
fn test_opencode_streaming_with_tool_use_events() {
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};
    use std::cell::RefCell;
    use std::rc::Rc;

    // Create a TestPrinter to capture output
    let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
    let mut parser =
        OpenCodeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, test_printer);

    // Simulate streaming tool_use events
    let input = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"started","input":{"filePath":"/test.rs"}}}}
{"type":"tool_use","timestamp":1768191346713,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/test.rs"}}}}"#;

    let reader = Cursor::new(input);

    // Verify the parse succeeds
    let workspace = MemoryWorkspace::new_test();
    let result = parser.parse_stream(reader, &workspace);
    assert!(
        result.is_ok(),
        "parse_stream should succeed for OpenCode events"
    );
}

/// Test that `with_terminal_mode` method works correctly
#[test]
#[cfg(feature = "test-utils")]
fn test_with_terminal_mode() {
    use crate::json_parser::terminal::TerminalMode;

    // Test that TerminalMode::None suppresses per-delta output (flushed at completion)
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None);

    // In non-TTY modes, text deltas are suppressed to prevent repeated prefixed lines
    let json = r#"{"type":"text","timestamp":1768191347231,"sessionID":"test","part":{"id":"prt_001","type":"text","text":"Hello"}}"#;
    let output = parser.parse_event(json);
    assert!(
        output.is_none(),
        "text delta should be suppressed in TerminalMode::None"
    );

    // Test that TerminalMode::Full produces streaming output
    let parser_full = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);
    let output_full = parser_full.parse_event(json);
    assert!(
        output_full.is_some(),
        "text delta should produce output in TerminalMode::Full"
    );
}

#[test]
fn test_opencode_parser_writes_commit_message_xml_when_commit_tag_seen() {
    use crate::workspace::Workspace;
    use std::path::Path;

    let mut parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);

    let input = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"id":"prt_1","sessionID":"ses_test","messageID":"msg_1","type":"step-start","snapshot":"deadbeef"}}
{"type":"text","timestamp":2,"sessionID":"ses_test","part":{"id":"prt_2","sessionID":"ses_test","messageID":"msg_1","type":"text","text":"<ralph-commit><ralph-subject>fix: test</ralph-subject></ralph-commit>"}}
{"type":"step_finish","timestamp":3,"sessionID":"ses_test","part":{"id":"prt_3","sessionID":"ses_test","messageID":"msg_1","type":"step-finish","reason":"stop"}}"#;

    let reader = Cursor::new(input);
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    parser
        .parse_stream(reader, &workspace)
        .expect("parse_stream should succeed");

    let xml_path = Path::new(".agent/tmp/commit_message.xml");
    assert!(
        workspace.exists(xml_path),
        "expected parser to write commit_message.xml when <ralph-commit> is present"
    );

    let xml = workspace
        .read(xml_path)
        .expect("expected commit_message.xml to be readable");
    assert!(xml.contains("<ralph-commit>"));
    assert!(xml.contains("<ralph-subject>fix: test</ralph-subject>"));
    assert!(xml.contains("</ralph-commit>"));
}

#[test]
fn test_opencode_parser_writes_issues_xml_when_issues_tag_seen() {
    use crate::workspace::Workspace;
    use std::path::Path;

    let mut parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);

    let input = r#"{"type":"step_start","timestamp":1,"sessionID":"ses_test","part":{"id":"prt_1","sessionID":"ses_test","messageID":"msg_1","type":"step-start","snapshot":"deadbeef"}}
{"type":"text","timestamp":2,"sessionID":"ses_test","part":{"id":"prt_2","sessionID":"ses_test","messageID":"msg_1","type":"text","text":"<ralph-issues><ralph-issue>First issue</ralph-issue></ralph-issues>"}}
{"type":"step_finish","timestamp":3,"sessionID":"ses_test","part":{"id":"prt_3","sessionID":"ses_test","messageID":"msg_1","type":"step-finish","reason":"stop"}}"#;

    let reader = Cursor::new(input);
    let workspace = MemoryWorkspace::new_test().with_dir(".agent/tmp");

    parser
        .parse_stream(reader, &workspace)
        .expect("parse_stream should succeed");

    let xml_path = Path::new(".agent/tmp/issues.xml");
    assert!(
        workspace.exists(xml_path),
        "expected parser to write issues.xml when <ralph-issues> is present"
    );

    let xml = workspace
        .read(xml_path)
        .expect("expected issues.xml to be readable");
    assert!(xml.contains("<ralph-issues>"));
    assert!(xml.contains("<ralph-issue>First issue</ralph-issue>"));
    assert!(xml.contains("</ralph-issues>"));
}
