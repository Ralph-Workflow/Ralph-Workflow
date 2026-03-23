//! Regression test for duplicate `item.completed` events.
//!
//! Codex can emit duplicate `item.completed` events for the same `agent_message`.
//! This must not cause the final agent message to be printed more than once in
//! non-TTY modes (Basic/None).
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::codex::CodexParser;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::BufReader;
use std::rc::Rc;

#[test]
fn test_codex_duplicate_item_completed_agent_message_prints_once_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"turn.started"}
{"type":"item.started","item":{"type":"agent_message","text":"Hello"}}
{"type":"item.started","item":{"type":"agent_message","text":" World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        assert_eq!(output.lines().count(),
            2,
            "Expected exactly two output lines (turn started + message) in Basic mode. Output:\n{output}"
        );
        assert!(
            output.contains("[ccs/codex]"),
            "Expected prefix in output. Output:\n{output}"
        );
        assert!(
            output.contains("Hello World"),
            "Expected final message in output. Output:\n{output}"
        );

        let prefix_count = output.matches("[ccs/codex]").count();
        assert_eq!(
            prefix_count, 2,
            "Expected prefix to be printed on both lines in Basic mode. Output:\n{output}"
        );
    });
}

#[test]
fn test_codex_duplicate_item_completed_agent_message_prints_once_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        let stream = r#"
{"type":"turn.started"}
{"type":"item.started","item":{"type":"agent_message","text":"Hello"}}
{"type":"item.started","item":{"type":"agent_message","text":" World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        assert_eq!(output.lines().count(),
            2,
            "Expected exactly two output lines (turn started + message) in None mode. Output:\n{output}"
        );
        assert!(
            output.contains("[ccs/codex]"),
            "Expected prefix in output. Output:\n{output}"
        );
        assert!(
            output.contains("Hello World"),
            "Expected final message in output. Output:\n{output}"
        );

        let prefix_count = output.matches("[ccs/codex]").count();
        assert_eq!(
            prefix_count, 2,
            "Expected prefix to be printed on both lines in None mode. Output:\n{output}"
        );
    });
}

#[test]
fn test_codex_agent_message_multiple_turns_prints_prefix_once_per_turn_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Two turns, each with an agent_message stream.
        // Append-only tracking must reset at `turn.started` so the first delta of the
        // second turn re-emits the prefix rather than emitting only a suffix.
        let stream = r#"
{"type":"turn.started","turn_id":"t1"}
{"type":"item.started","item":{"type":"agent_message","text":"Hello"}}
{"type":"item.started","item":{"type":"agent_message","text":" World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}
{"type":"turn.started","turn_id":"t2"}
{"type":"item.started","item":{"type":"agent_message","text":"Goodbye"}}
{"type":"item.started","item":{"type":"agent_message","text":" World"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Goodbye World"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // In None mode, Codex emits a visible "Turn started" line per turn *plus*
        // a final line for each completed message.
        assert_eq!(output.lines().count(),
            4,
            "Expected exactly four output lines (turn started + message per turn) in None mode. Output:\n{output}"
        );

        // Prefix should be present for each of the 4 lines.
        let prefix_count = output.matches("[ccs/codex]").count();
        assert_eq!(
            prefix_count, 4,
            "Expected prefix to be printed for each line in None mode. Output:\n{output}"
        );

        // The key behavioral assertion: each completed message is rendered with the prefix.
        assert!(
            output.contains("[ccs/codex] Hello World"),
            "Expected first turn message to include prefix. Output:\n{output}"
        );
        assert!(
            output.contains("[ccs/codex] Goodbye World"),
            "Expected second turn message to include prefix. Output:\n{output}"
        );
    });
}
