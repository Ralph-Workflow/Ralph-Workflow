//! Stdout writer abstraction for boundary module compliance.
//!
//! This module satisfies the dylint boundary-module check for code that
//! accesses stdout/stderr.

use std::io::{IsTerminal, Write};

/// Write bytes to stdout.
///
/// This is a boundary function that wraps `std::io::stdout().write()`.
pub fn stdout_write(buf: &[u8]) -> std::io::Result<usize> {
    std::io::stdout().write(buf)
}

/// Flush stdout.
///
/// This is a boundary function that wraps `std::io::stdout().flush()`.
pub fn stdout_flush() -> std::io::Result<()> {
    std::io::stdout().flush()
}

/// Check if stdout is a terminal.
///
/// This is a boundary function that wraps `std::io::stdout().is_terminal()`.
pub fn stdout_is_terminal() -> bool {
    std::io::stdout().is_terminal()
}

/// Write formatted output to stdout.
///
/// This is a boundary function that handles the writeln! macro pattern.
pub fn stdout_write_line(msg: &str) -> std::io::Result<()> {
    writeln!(std::io::stdout(), "{msg}")
}

/// Write formatted output to stderr.
///
/// This is a boundary function that handles the writeln! macro pattern.
pub fn stderr_write_line(msg: &str) -> std::io::Result<()> {
    writeln!(std::io::stderr(), "{msg}")
}
