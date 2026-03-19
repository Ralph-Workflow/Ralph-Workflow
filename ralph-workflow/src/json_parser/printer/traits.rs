// Printer trait and standard implementations.
//
// Contains the Printable trait and StdoutPrinter/StderrPrinter.

use std::cell::RefCell;
use std::rc::Rc;

/// Trait for output destinations in parsers.
///
/// This trait allows parsers to write to different output destinations
/// (stdout, stderr, or test collectors) without hardcoding the specific
/// destination. This makes parsers testable by allowing output capture.
pub trait Printable {
    /// Check if this printer is connected to a terminal.
    ///
    /// This is used to determine whether to use terminal-specific features
    /// like colors and carriage return-based updates.
    fn is_terminal(&self) -> bool;
}

/// Printer that writes to stdout.
#[derive(Debug, Clone)]
pub struct StdoutPrinter {
    buffer: String,
    is_terminal: bool,
}

impl StdoutPrinter {
    /// Create a new stdout printer.
    #[must_use]
    pub fn new() -> Self {
        Self {
            buffer: String::new(),
            is_terminal: std::io::stdout().is_terminal(),
        }
    }

    /// Write text to the buffer.
    #[must_use]
    pub fn write_text(self, text: &str) -> Self {
        Self {
            buffer: format!("{}{}", self.buffer, text),
            is_terminal: self.is_terminal,
        }
    }

    /// Write a line to the buffer.
    #[must_use]
    pub fn write_line(self, line: &str) -> Self {
        self.write_text(&format!("{}\n", line))
    }

    /// Emit the buffered content and reset the buffer.
    /// Returns (printer with empty buffer, emitted content).
    #[must_use]
    pub fn emit(self) -> (Self, String) {
        let output = self.buffer.clone();
        (
            Self {
                buffer: String::new(),
                is_terminal: self.is_terminal,
            },
            output,
        )
    }

    /// Get the current buffer content.
    #[must_use]
    pub fn get_buffer(&self) -> &str {
        &self.buffer
    }

    /// Flush the printer (no-op for stdout, kept for trait compatibility).
    pub fn flush(self) -> Self {
        self
    }
}

impl Default for StdoutPrinter {
    fn default() -> Self {
        Self::new()
    }
}

impl Printable for StdoutPrinter {
    fn is_terminal(&self) -> bool {
        self.is_terminal
    }
}

impl std::io::Write for StdoutPrinter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let s =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        self.buffer.push_str(s);
        std::io::stdout().write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        std::io::stdout().flush()
    }
}

/// Printer that writes to stderr.
#[derive(Debug, Clone)]
#[cfg(any(test, feature = "test-utils"))]
pub struct StderrPrinter {
    buffer: String,
    is_terminal: bool,
}

#[cfg(any(test, feature = "test-utils"))]
impl StderrPrinter {
    /// Create a new stderr printer.
    #[must_use]
    pub fn new() -> Self {
        Self {
            buffer: String::new(),
            is_terminal: std::io::stderr().is_terminal(),
        }
    }

    /// Write text to the buffer.
    #[must_use]
    pub fn write_text(self, text: &str) -> Self {
        Self {
            buffer: format!("{}{}", self.buffer, text),
            is_terminal: self.is_terminal,
        }
    }

    /// Write a line to the buffer.
    #[must_use]
    pub fn write_line(self, line: &str) -> Self {
        self.write_text(&format!("{}\n", line))
    }

    /// Emit the buffered content and reset the buffer.
    /// Returns (printer with empty buffer, emitted content).
    #[must_use]
    pub fn emit(self) -> (Self, String) {
        let output = self.buffer.clone();
        (
            Self {
                buffer: String::new(),
                is_terminal: self.is_terminal,
            },
            output,
        )
    }

    /// Get the current buffer content.
    #[must_use]
    pub fn get_buffer(&self) -> &str {
        &self.buffer
    }

    /// Flush the printer (no-op for stderr, kept for trait compatibility).
    pub fn flush(self) -> Self {
        self
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Default for StderrPrinter {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl std::io::Write for StderrPrinter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let s =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        self.buffer.push_str(s);
        std::io::stderr().write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        std::io::stderr().flush()
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for StderrPrinter {
    fn is_terminal(&self) -> bool {
        self.is_terminal
    }
}

/// Shared printer reference for use in parsers.
///
/// This type alias represents a shared, mutable reference to a printer
/// that can be used across parser methods.
#[deprecated(
    since = "0.6.0",
    note = "Use pure Printer types with explicit state threading instead"
)]
pub type SharedPrinter = Rc<RefCell<dyn Printable>>;

/// Create a shared stdout printer.
#[deprecated(
    since = "0.6.0",
    note = "Use pure Printer types with explicit state threading instead"
)]
#[must_use]
pub fn shared_stdout() -> SharedPrinter {
    Rc::new(RefCell::new(StdoutPrinter::new()))
}
