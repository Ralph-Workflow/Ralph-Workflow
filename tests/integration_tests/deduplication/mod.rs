//! Integration tests for streaming output deduplication.
//!
//! These tests verify that users see correct output on their terminal:
//! 1. No duplicate visible content from streaming glitches
//! 2. Snapshot repairs work (accumulated content re-sent as delta)
//! 3. Assistant events don't duplicate streaming content
//! 4. Intentional repetition is preserved (e.g., "echo echo echo")
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (visible terminal output), not internal state
//! - Uses `VirtualTerminal` to mock at architectural boundary (terminal I/O)
//! - Tests are deterministic and isolated
//!
//! # Testing Strategy
//!
//! We use `VirtualTerminal` which accurately simulates real terminal behavior:
//! - Carriage return (`\r`) moves cursor to column 0
//! - ANSI clear line (`\x1b[2K`) erases line content
//! - ANSI cursor up/down (`\x1b[1A`, `\x1b[1B`) for in-place updates
//! - Text overwrites previous content when cursor is repositioned
//!
//! This tests what users ACTUALLY SEE, not just what bytes were written.

use std::cell::RefCell;
use std::io::{BufReader, Cursor};
use std::rc::Rc;

use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::printer::{SharedPrinter, VirtualTerminal};
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::json_parser::ClaudeParser;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;

// =============================================================================
// Helper Functions
// =============================================================================

/// Create a Claude parser with `VirtualTerminal` in Full mode (ANSI sequences enabled).
pub fn create_parser_with_vterm() -> (ClaudeParser, Rc<RefCell<VirtualTerminal>>) {
    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);
    (parser, vterm)
}

/// Parse events and return the `VirtualTerminal` for inspection.
pub fn parse_events(events: &[&str]) -> Rc<RefCell<VirtualTerminal>> {
    let (mut parser, vterm) = create_parser_with_vterm();
    let workspace = MemoryWorkspace::new_test();
    let input = events.join("\n");
    let cursor = Cursor::new(input);
    let reader = BufReader::new(cursor);
    parser
        .parse_stream(reader, &workspace)
        .expect("parse_stream should succeed");
    vterm
}

mod assistant_events;
mod edge_cases;
mod streaming;
