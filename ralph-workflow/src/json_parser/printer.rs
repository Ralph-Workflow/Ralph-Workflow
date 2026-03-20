//! Printer abstraction for testable output.
//!
//! This module provides a trait-based abstraction for output destinations,
//! allowing parsers to write to stdout, stderr, or test collectors without
//! changing their core logic.

use std::cell::RefCell;
use std::io::{self, IsTerminal};
use std::rc::Rc;

// Trait and standard printers
include!("printer/traits.rs");

// Test printer (test-utils only)
include!("printer/io_test_printer.rs");

// Streaming test printer (test-utils only)
include!("printer/streaming_printer.rs");

// Virtual terminal (test-utils only)
include!("printer/virtual_terminal.rs");

// Tests
include!("printer/io_tests.rs");
