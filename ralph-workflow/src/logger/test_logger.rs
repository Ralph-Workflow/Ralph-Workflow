//! Test logger for capturing log output in tests.
//!
//! Provides `TestLogger` which implements `Loggable` and captures all log
//! messages in memory for assertion in tests.

use std::cell::RefCell;

use super::loggable::Loggable;
use crate::json_parser::printer::Printable;

/// Test logger that captures log output for assertion.
///
/// This logger stores all log messages in memory for testing purposes.
/// It provides methods to retrieve and inspect the captured log output.
/// Uses line buffering similar to `TestPrinter` to handle partial writes.
///
/// # Design Note
///
/// This logger uses interior mutability (RefCell) to allow the Loggable trait's
/// `&self` methods to accumulate state. This is necessary because the Loggable
/// trait uses `&self` and we need to store accumulated logs. This is acceptable
/// for a test utility that doesn't share state across threads.
///
/// # Availability
///
/// `TestLogger` is available in test builds (`#[cfg(any(test, feature = "test-utils"))]`) and when the
/// `test-utils` feature is enabled (for integration tests). In production
/// binary builds with `--all-features`, the `test-utils` feature enables
/// this code but it's not used by the binary, which is expected behavior.
#[derive(Debug, Default)]
pub struct TestLogger {
    logs: RefCell<Vec<String>>,
    buffer: RefCell<String>,
}

impl TestLogger {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn get_logs(&self) -> Vec<String> {
        let mut result = self.logs.borrow().clone();
        if !self.buffer.borrow().is_empty() {
            result.push(self.buffer.borrow().clone());
        }
        result
    }

    pub fn clear(&self) {
        self.logs.borrow_mut().clear();
        self.buffer.borrow_mut().clear();
    }

    pub fn has_log(&self, msg: &str) -> bool {
        self.get_logs().iter().any(|l| l.contains(msg))
    }

    pub fn count_pattern(&self, pattern: &str) -> usize {
        self.get_logs()
            .iter()
            .filter(|l| l.contains(pattern))
            .count()
    }
}

impl Loggable for TestLogger {
    fn log(&self, msg: &str) {
        self.logs.borrow_mut().push(msg.to_string());
    }

    fn info(&self, msg: &str) {
        self.log(&format!("[INFO] {msg}"));
    }

    fn success(&self, msg: &str) {
        self.log(&format!("[OK] {msg}"));
    }

    fn warn(&self, msg: &str) {
        self.log(&format!("[WARN] {msg}"));
    }

    fn error(&self, msg: &str) {
        self.log(&format!("[ERROR] {msg}"));
    }
}

impl Printable for TestLogger {
    fn is_terminal(&self) -> bool {
        false
    }
}

impl std::io::Write for TestLogger {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let s = std::str::from_utf8(buf)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        self.buffer.borrow_mut().push_str(s);

        while let Some(newline_pos) = self.buffer.borrow().find('\n') {
            let line = self
                .buffer
                .borrow_mut()
                .drain(..=newline_pos)
                .collect::<String>();
            self.logs.borrow_mut().push(line);
        }

        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        if !self.buffer.borrow().is_empty() {
            self.logs.borrow_mut().push(self.buffer.borrow().clone());
            self.buffer.borrow_mut().clear();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_logger_captures_output() {
        let logger = TestLogger::new();
        logger.log("Test message");
        assert!(logger.has_log("Test message"));
    }

    #[test]
    fn test_logger_get_logs() {
        let logger = TestLogger::new();
        logger.log("Message 1");
        logger.log("Message 2");
        let logs = logger.get_logs();
        assert_eq!(logs.len(), 2);
        assert_eq!(logs[0], "Message 1");
        assert_eq!(logs[1], "Message 2");
    }

    #[test]
    fn test_logger_clear() {
        let logger = TestLogger::new();
        logger.log("Before clear");
        assert!(!logger.get_logs().is_empty());
        logger.clear();
        assert!(logger.get_logs().is_empty());
    }

    #[test]
    fn test_logger_count_pattern() {
        let logger = TestLogger::new();
        logger.log("test message 1");
        logger.log("test message 2");
        logger.log("other message");
        assert_eq!(logger.count_pattern("test"), 2);
    }
}
