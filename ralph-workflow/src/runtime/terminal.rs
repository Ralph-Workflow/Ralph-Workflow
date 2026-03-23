//! Terminal I/O utilities in the runtime boundary.
//!
//! This module provides terminal-related capabilities.

/// Write a message to stdout.
pub fn stdout_write(message: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut stdout = std::io::stdout();
    stdout.write_all(message.as_bytes())?;
    stdout.flush()?;
    Ok(())
}

/// Write a message to stderr.
pub fn stderr_write(message: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut stderr = std::io::stderr();
    stderr.write_all(message.as_bytes())?;
    stderr.flush()?;
    Ok(())
}
