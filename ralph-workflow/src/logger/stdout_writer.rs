use std::io::{IsTerminal, Write};

pub fn stdout_write(buf: &[u8]) -> std::io::Result<usize> {
    std::io::stdout().write(buf)
}

pub fn stdout_flush() -> std::io::Result<()> {
    std::io::stdout().flush()
}

pub fn stdout_is_terminal() -> bool {
    std::io::stdout().is_terminal()
}

pub fn stdout_write_line(msg: &str) -> std::io::Result<()> {
    writeln!(std::io::stdout(), "{msg}")
}

pub fn stderr_write_line(msg: &str) -> std::io::Result<()> {
    writeln!(std::io::stderr(), "{msg}")
}
