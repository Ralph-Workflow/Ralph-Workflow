//! Core Logger implementation.
//!
//! Provides structured, colorized logging output for Ralph's pipeline
//! with support for file logging and various log levels.
//!
//! # Logger Design and Behavior
//!
//! The `Logger` struct provides dual-output logging:
//! - **Console output**: Colorized, human-readable messages to stdout/stderr
//! - **File output**: Plain text (ANSI codes stripped) with timestamps
//!
//! ## How Logger Writes to Files
//!
//! Logger's file logging is **not** done through the `std::io::Write` trait.
//! Instead, file logging happens via the `Loggable` trait's `log()` method, which
//! is called by each log level method (`info()`, `success()`, `warn()`, `error()`).
//!
//! ### Important: `Write` Trait Behavior
//!
//! Logger implements `std::io::Write`, but the `write()` method **only writes to
//! stdout**, NOT to the log file. This is intentional design:
//!
//! ```ignore
//! let logger = Logger::new(Colors::new()).with_log_file("app.log");
//!
//! // This writes to BOTH console AND file:
//! logger.info("This message goes everywhere");
//!
//! // This writes ONLY to console (via Write trait):
//! writeln!(logger, "This only goes to console").unwrap();
//! ```
//!
//! If you need file output, always use the Logger's methods (`info()`, `success()`,
//! etc.) rather than the `Write` trait. The `Write` trait implementation exists
//! for compatibility with code that expects a writer, but it's a console-only path.
//!
//! ## Using the `Loggable` Trait
//!
//! The `Loggable` trait provides a unified interface for logging that works with
//! both `Logger` (production) and `TestLogger` (testing):
//!
//! ```ignore
//! use ralph_workflow::logger::Loggable;
//!
//! fn process_logs<L: Loggable>(logger: &L) {
//!     logger.info("Starting process");
//!     logger.success("Process completed");
//!     logger.warn("Potential issue");
//!     logger.error("Critical error");
//! }
//! ```
//!
//! ## Logger → File → XML Flow
//!
//! The pipeline treats structured output as explicit XML files written to
//! `.agent/tmp/` by the agent. The reducer then validates and archives these
//! files via effects. Logger output is for diagnostics only.
//!
//! ### Testing Logger Output
//!
//! For testing, use `TestLogger` from this module:
//!
//! ```ignore
//! use ralph_workflow::logger::output::TestLogger;
//! use ralph_workflow::logger::Loggable;
//!
//! let logger = TestLogger::new();
//! logger.info("Test message");
//!
//! assert!(logger.has_log("Test message"));
//! assert!(logger.has_log("[INFO]"));
//! ```
//!
//! `TestLogger` follows the same pattern as `TestPrinter` with line buffering
//! and implements the same traits (`Printable`, `std::io::Write`, `Loggable`).
//! Note: `TestLogger` is available in test builds and when the `test-utils`
//! feature is enabled (for integration tests).

// Sub-modules for split functionality
#[cfg(any(test, feature = "test-utils"))]
#[path = "io_test_logger.rs"]
mod io_test_logger;
#[path = "loggable.rs"]
mod loggable;
#[path = "logger_impl.rs"]
mod logger_impl;
#[path = "output_formatting.rs"]
mod output_formatting;

// Re-export sub-module items
#[cfg(any(test, feature = "test-utils"))]
pub use io_test_logger::TestLogger;
pub use loggable::Loggable;
pub use logger_impl::Logger;
pub use output_formatting::{argv_requests_json, format_generic_json_for_display};

#[cfg(test)]
mod tests {
    use crate::logger::strip_ansi_codes;

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
