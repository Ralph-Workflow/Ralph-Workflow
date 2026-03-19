use std::io::{IsTerminal, Write};
use std::sync::LazyLock;

pub static ANSI_RE: LazyLock<Result<regex::Regex, regex::Error>> =
    LazyLock::new(|| regex::Regex::new(r"\x1b\[[0-9;]*m"));

#[must_use]
pub fn strip_ansi_codes(s: &str) -> String {
    ANSI_RE
        .as_ref()
        .map_or_else(|_| s.to_string(), |re| re.replace_all(s, "").to_string())
}

pub trait ColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String>;
    fn is_terminal(&self) -> bool;
}

pub struct RealColorEnvironment;

impl ColorEnvironment for RealColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::stdout().is_terminal()
    }
}

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_ansi_codes() {
        let input = "\x1b[31mred\x1b[0m text";
        assert_eq!(strip_ansi_codes(input), "red text");
    }

    #[test]
    fn test_strip_ansi_codes_no_codes() {
        let input = "plain text";
        assert_eq!(strip_ansi_codes(input), "plain text");
    }

    #[test]
    fn test_strip_ansi_codes_multiple() {
        let input = "\x1b[1m\x1b[32mbold green\x1b[0m \x1b[34mblue\x1b[0m";
        assert_eq!(strip_ansi_codes(input), "bold green blue");
    }
}
