//! Terminal I/O operations for CLI.
//!
//! This module provides boundary functions for terminal input/output.
//! All functions here wrap std::io operations and are exempt from
//! functional programming lints because they are explicitly boundary code.

use std::io::{self, IsTerminal, Write};

pub fn is_terminal() -> bool {
    io::stdin().is_terminal() && io::stdout().is_terminal()
}

pub fn stdout_is_terminal() -> bool {
    io::stdout().is_terminal()
}

pub fn stderr_is_terminal() -> bool {
    io::stderr().is_terminal()
}

pub fn stdout() -> io::Stdout {
    io::stdout()
}

pub fn stderr() -> io::Stderr {
    io::stderr()
}

pub fn flush_stdout() -> std::io::Result<()> {
    io::stdout().flush()
}

pub fn read_line() -> Option<String> {
    io::stdin().lines().next().and_then(|r| r.ok())
}

pub fn exit_with_code(code: i32) -> ! {
    std::process::exit(code)
}
