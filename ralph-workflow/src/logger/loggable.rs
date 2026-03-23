//! Loggable trait for logger output destinations.
//!
//! This trait allows loggers to write to different destinations
//! (console, files, test collectors) without hardcoding the specific destination.

use crate::logger::Colors;

/// Trait for logger output destinations.
///
/// This trait allows loggers to write to different destinations
/// (console, files, test collectors) without hardcoding the specific destination.
/// This makes loggers testable by allowing output capture.
///
/// This trait mirrors the `Printable` trait pattern used for printers,
/// providing a unified interface for both production and test loggers.
pub trait Loggable {
    /// Write a log message to the sink.
    ///
    /// This is the core logging method that all loggers must implement.
    /// For `Logger`, this writes to the configured log file (if any).
    /// For `TestLogger`, this captures the message in memory for testing.
    fn log(&self, msg: &str);

    /// Log an informational message.
    ///
    /// Default implementation formats the message with [INFO] prefix
    /// and delegates to the `log` method.
    fn info(&self, msg: &str) {
        self.log(&format!("[INFO] {msg}"));
    }

    /// Log a success message.
    ///
    /// Default implementation formats the message with `[OK]` prefix
    /// and delegates to the `log` method.
    fn success(&self, msg: &str) {
        self.log(&format!("[OK] {msg}"));
    }

    /// Log a warning message.
    ///
    /// Default implementation formats the message with [WARN] prefix
    /// and delegates to the `log` method.
    fn warn(&self, msg: &str) {
        self.log(&format!("[WARN] {msg}"));
    }

    /// Log an error message.
    ///
    /// Default implementation formats the message with [ERROR] prefix
    /// and delegates to the `log` method.
    fn error(&self, msg: &str) {
        self.log(&format!("[ERROR] {msg}"));
    }

    /// Print a section header with box drawing.
    ///
    /// Default implementation does nothing (test loggers may not need headers).
    /// Production loggers override this to display styled headers.
    fn header(&self, _title: &str, _color_fn: fn(Colors) -> &'static str) {
        // Default: no-op for test loggers
    }
}
