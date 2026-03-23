//! Comprehensive CCS spam prevention verification.
//!
//! This test module serves as the definitive verification that the three-layer
//! spam prevention architecture works correctly for ALL CCS streaming scenarios.
//!
//! # Three-Layer Spam Prevention Architecture
//!
//! ## Layer 1: Suppression at Renderer Level
//!
//! Delta renderers (`TextDeltaRenderer`, `ThinkingDeltaRenderer`) return empty strings
//! in `TerminalMode::Basic` and `TerminalMode::None` for both `render_first_delta` and
//! `render_subsequent_delta`. This prevents per-delta spam at the source.
//!
//! **Implementation:** `ralph-workflow/src/json_parser/delta_display/renderer.rs`
//!
//! ## Layer 2: Accumulation in `StreamingSession`
//!
//! `StreamingSession` accumulates all content by (`ContentType`, index) across deltas.
//! This state is preserved across all delta events for a single message.
//!
//! **Implementation:** `ralph-workflow/src/json_parser/streaming_state/session.rs`
//!
//! ## Layer 3: Flush at Completion Boundaries
//!
//! Parser layer flushes accumulated content ONCE at completion boundaries:
//! - `ClaudeParser`: `handle_message_stop` in `claude/delta_handling.rs`
//! - `CodexParser`: `item.completed` handlers in `codex/event_handlers/*.rs`
//!
//! This ensures:
//! - **Full mode (TTY)**: In-place updates work normally with cursor positioning
//! - **Basic/None modes**: One prefixed line per content block, regardless of delta count
//!
//! # Comprehensive Test Coverage
//!
//! This architecture is validated by 53 regression tests across 8 test files:
//!
//! 1. **`ccs_nuclear_spam_test.rs`** (6 tests)
//!    - 500+ deltas per block with HARD assertions (`total_lines` <= 2-5)
//!    - Tests ccs/glm and ccs/codex
//!    - Covers text, thinking, tool input, and mixed deltas
//!    - No consecutive duplicates allowed
//!
//! 2. **`ccs_all_delta_types_spam_reproduction.rs`** (7 tests)
//!    - 1000+ deltas per block (ultra-extreme stress)
//!    - Rapid successive deltas
//!    - Interleaved blocks with many deltas
//!    - Multi-item scenarios for Codex
//!    - Same stream tested in different modes for consistency
//!
//! 3. **`ccs_streaming_spam_all_deltas.rs`** (15 tests)
//!    - All delta types (text, thinking, tool input)
//!    - Both agents (ccs/glm, ccs/codex)
//!    - Both non-TTY modes (None and Basic)
//!    - Multi-turn streaming scenarios
//!    - Real-world multi-block streaming
//!    - Extreme text deltas (200+ chunks)
//!    - Two text blocks flushed independently
//!
//! 4. **`ccs_nuclear_full_log_regression.rs`** (5 tests)
//!    - Real production logs with 12,000+ deltas
//!    - Per-block line count bounds (NUCLEAR assertions)
//!    - Both agents in both non-TTY modes
//!
//! 5. **`ccs_streaming_edge_cases.rs`** (7 tests)
//!    - Empty deltas
//!    - Rapid transitions between content types
//!    - Malformed/incomplete deltas
//!    - Cross-message contamination prevention
//!
//! 6. **`ccs_extreme_streaming_regression.rs`** (8 tests)
//!    - 500+ deltas per block
//!    - Multi-block scenarios
//!    - Tool input streaming
//!
//! 7. **`ccs_real_world_log_regression.rs`** (3 tests)
//!    - Production logs with 12,596 total deltas
//!    - Complex multi-block streaming
//!
//! 8. **`ccs_basic_mode_nuclear_test.rs`** (2 tests)
//!    - Basic mode (colors but no cursor positioning)
//!    - 500+ deltas verification
//!
//! # This Test's Purpose
//!
//! This test serves as a high-level smoke test that validates the architecture
//! works for representative scenarios. The 53 existing tests provide the
//! comprehensive coverage. This test ensures:
//!
//! 1. Both agents work correctly
//! 2. Both non-TTY modes work correctly
//! 3. All delta types work correctly
//! 4. Multi-delta scenarios produce bounded output
//! 5. Content is not lost during suppression
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::claude::ClaudeParser;
use ralph_workflow::json_parser::codex::CodexParser;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::fmt::Write;
use std::io::BufReader;
use std::rc::Rc;

/// Helper to count total non-empty lines
fn count_total_lines(output: &str) -> usize {
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .count()
}

/// Helper to check for consecutive duplicates
fn has_consecutive_duplicates(output: &str) -> Option<String> {
    let lines: Vec<&str> = output.lines().collect();
    for pair in lines.windows(2) {
        if !pair[0].is_empty() && pair[0] == pair[1] {
            return Some(pair[0].to_string());
        }
    }
    None
}

// ============================================================================
// Architecture Verification Tests
// ============================================================================

#[test]
fn test_ccs_glm_architecture_verification_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Test scenario: 300 deltas across 3 content blocks (100 each)
        // Expected: At most 3 lines (one per block), proving suppression works
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
"#,
        );

        // Block 1: 100 thinking deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );
        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"t{i} "}}}}}}"#
            ).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
"#,
        );

        // Block 2: 100 text deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":"w{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
"#,
        );

        // Block 3: 100 tool input deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
"#,
        );
        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":2,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"c{i} "}}}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
"#,
        );

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // HARD ASSERTION: With 300 deltas across 3 blocks, we should have AT MOST 4 total lines
        // (1 thinking + 1 text + 1 tool start + 1 tool input)
        let total_lines = count_total_lines(&output);
        assert!(
            total_lines <= 4,
            "Architecture verification FAILED: Expected <= 4 total lines for 300 deltas across 3 blocks, found {total_lines}.\n\n\
             This indicates the suppress-accumulate-flush architecture is broken!\n\n\
             Output:\n{output}"
        );

        // Verify no consecutive duplicates
        if let Some(dup) = has_consecutive_duplicates(&output) {
            panic!(
                "Architecture verification FAILED: Found consecutive duplicate line.\n\
                 Duplicate: '{dup}'\n\n\
                 Output:\n{output}"
            );
        }

        // Verify all content types are present (not lost)
        assert!(
            output.contains("t0") && output.contains("t99"),
            "Expected thinking content (t0...t99) to be present. Output:\n{output}"
        );
        assert!(
            output.contains("w0") && output.contains("w99"),
            "Expected text content (w0...w99) to be present. Output:\n{output}"
        );
        assert!(
            output.contains("c0") && output.contains("c99"),
            "Expected tool input content (c0...c99) to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_architecture_verification_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        // Test scenario: 200 text deltas
        // Expected: At most 1 line, proving suppression works in Basic mode too
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..200 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"w{i} "}}}}}}"#).unwrap();
        }

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // HARD ASSERTION: With 200 text deltas, we should have AT MOST 1 total line
        let total_lines = count_total_lines(&output);
        assert!(
            total_lines <= 1,
            "Architecture verification FAILED (Basic mode): Expected <= 1 total line for 200 text deltas, found {total_lines}.\n\n\
             This indicates the suppress-accumulate-flush architecture is broken in Basic mode!\n\n\
             Output:\n{output}"
        );

        // Verify content is present
        assert!(
            output.contains("w0") && output.contains("w199"),
            "Expected content (w0...w199) to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_architecture_verification_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Test scenario: 150 reasoning deltas + 150 agent_message deltas = 300 total
        // Expected: At most 2 lines (one per item type), proving suppression works
        let mut stream = String::new();

        // 150 reasoning deltas
        for i in 0..150 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"r{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"reasoning"}}
"#,
        );

        // 150 agent_message deltas
        for i in 0..150 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"m{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // HARD ASSERTION: With 300 deltas across 2 item types, we should have AT MOST 2 total lines
        let total_lines = count_total_lines(&output);
        assert!(
            total_lines <= 2,
            "Architecture verification FAILED (Codex): Expected <= 2 total lines for 300 deltas, found {total_lines}.\n\n\
             This indicates the suppress-accumulate-flush architecture is broken for Codex!\n\n\
             Output:\n{output}"
        );

        // Verify no consecutive duplicates
        if let Some(dup) = has_consecutive_duplicates(&output) {
            panic!(
                "Architecture verification FAILED (Codex): Found consecutive duplicate line.\n\
                 Duplicate: '{dup}'\n\n\
                 Output:\n{output}"
            );
        }

        // Verify all content is present
        assert!(
            output.contains("r0") && output.contains("r149"),
            "Expected reasoning content (r0...r149) to be present. Output:\n{output}"
        );
        assert!(
            output.contains("m0") && output.contains("m149"),
            "Expected agent_message content (m0...m149) to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_architecture_verification_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        // Test scenario: 200 agent_message deltas
        // Expected: At most 1 line, proving suppression works in Basic mode
        let mut stream = String::new();

        for i in 0..200 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"m{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // HARD ASSERTION: With 200 deltas, we should have AT MOST 1 total line
        let total_lines = count_total_lines(&output);
        assert!(
            total_lines <= 1,
            "Architecture verification FAILED (Codex Basic mode): Expected <= 1 total line for 200 deltas, found {total_lines}.\n\n\
             This indicates the suppress-accumulate-flush architecture is broken for Codex in Basic mode!\n\n\
             Output:\n{output}"
        );

        // Verify content is present
        assert!(
            output.contains("m0") && output.contains("m199"),
            "Expected content (m0...m199) to be present. Output:\n{output}"
        );
    });
}
