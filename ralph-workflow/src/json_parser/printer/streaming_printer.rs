// Streaming test printer implementation.
//
// Contains StreamingTestPrinter for capturing individual write calls.

/// Record of a single `write()` call for streaming analysis.
///
/// Captures the content and timestamp of each write operation,
/// allowing tests to verify incremental streaming behavior.
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone)]
pub struct WriteCall {
    /// The content written in this call.
    pub content: String,
    /// Timestamp when write occurred.
    pub timestamp: std::time::Instant,
}

/// Record of a `flush()` call with metadata.
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone)]
pub struct FlushCall {
    /// Index of the last write before this flush (None if no writes yet).
    pub last_write_index: Option<usize>,
    /// Timestamp when flush occurred.
    pub timestamp: std::time::Instant,
}

/// Test printer that captures EVERY `write()` call for streaming verification.
///
/// Unlike [`TestPrinter`] which processes complete lines, this tracks:
/// - Each individual `write()` call as a separate record
/// - Each `flush()` call for verifying real-time output behavior
/// - Content progression over time
/// - Timing between writes for streaming analysis
///
/// Use this to verify that streaming produces incremental output
/// (multiple small writes) rather than batched output (one large write).
///
/// # Example
///
/// ```ignore
/// use ralph_workflow::json_parser::printer::{StreamingTestPrinter, Printable};
/// use std::io::Write;
///
/// let mut printer = StreamingTestPrinter::new();
/// printer.write_all(b"Hello").unwrap();
/// printer.flush().unwrap();
/// printer.write_all(b" World").unwrap();
/// printer.flush().unwrap();
///
/// assert_eq!(printer.write_count(), 2);
/// assert_eq!(printer.flush_count(), 2);
/// assert!(printer.get_full_output().contains("Hello World"));
/// ```
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug)]
pub struct StreamingTestPrinter {
    /// Each individual `write()` call recorded.
    write_calls: RefCell<Vec<WriteCall>>,
    /// Each `flush()` call recorded.
    flush_calls: RefCell<Vec<FlushCall>>,
    /// Simulated terminal status for testing different terminal modes.
    simulated_is_terminal: bool,
}

#[cfg(any(test, feature = "test-utils"))]
impl StreamingTestPrinter {
    /// Create a new streaming test printer (simulates non-terminal).
    #[must_use]
    pub const fn new() -> Self {
        Self {
            write_calls: RefCell::new(Vec::new()),
            flush_calls: RefCell::new(Vec::new()),
            simulated_is_terminal: false,
        }
    }

    /// Create a new streaming test printer with specified terminal simulation.
    ///
    /// # Arguments
    /// * `is_terminal` - Whether to simulate being connected to a terminal
    #[must_use]
    pub const fn new_with_terminal(is_terminal: bool) -> Self {
        Self {
            write_calls: RefCell::new(Vec::new()),
            flush_calls: RefCell::new(Vec::new()),
            simulated_is_terminal: is_terminal,
        }
    }

    /// Get all write calls for inspection.
    pub fn get_write_calls(&self) -> Vec<WriteCall> {
        self.write_calls.borrow().clone()
    }

    /// Get the number of `write()` calls made.
    pub fn write_count(&self) -> usize {
        self.write_calls.borrow().len()
    }

    /// Get the full output (all writes concatenated).
    pub fn get_full_output(&self) -> String {
        self.write_calls
            .borrow()
            .iter()
            .map(|w| w.content.clone())
            .collect()
    }

    /// Get the content at a specific write index.
    pub fn get_content_at_write(&self, index: usize) -> Option<String> {
        self.write_calls
            .borrow()
            .get(index)
            .map(|w| w.content.clone())
    }

    /// Verify that multiple incremental writes occurred.
    ///
    /// # Arguments
    /// * `min_expected` - Minimum number of writes expected
    ///
    /// # Returns
    /// `Ok(())` if at least `min_expected` writes occurred, `Err` with details otherwise.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn verify_incremental_writes(&self, min_expected: usize) -> Result<(), String> {
        let count = self.write_count();
        if count >= min_expected {
            Ok(())
        } else {
            Err(format!(
                "Expected at least {min_expected} incremental writes, but only {count} occurred. \
                 This suggests output is batched rather than streamed."
            ))
        }
    }

    /// Check if the output contains a specific ANSI escape sequence.
    pub fn contains_escape_sequence(&self, seq: &str) -> bool {
        self.get_full_output().contains(seq)
    }

    /// Check if any ANSI escape sequences are present in the output.
    pub fn has_any_escape_sequences(&self) -> bool {
        self.get_full_output().contains('\x1b')
    }

    /// Strip ANSI escape sequences from a string.
    ///
    /// Uses a simple state machine approach to remove all ANSI codes.
    #[must_use]
    pub fn strip_ansi(s: &str) -> String {
        let mut result = String::with_capacity(s.len());
        let chars: Vec<char> = s.chars().collect();
        let mut i = 0;

        while i < chars.len() {
            let c = chars[i];
            if c == '\x1b' && i + 1 < chars.len() && chars[i + 1] == '[' {
                i += 2;
                while i < chars.len() && !chars[i].is_ascii_alphabetic() {
                    i += 1;
                }
                if i < chars.len() {
                    i += 1;
                }
            } else {
                result.push(c);
                i += 1;
            }
        }
        result
    }

    /// Get the content progression across all writes (ANSI stripped).
    ///
    /// Returns a vector of accumulated content at each write point,
    /// useful for verifying that content grows incrementally.
    pub fn get_content_progression(&self) -> Vec<String> {
        let mut accumulated = String::new();

        self.write_calls
            .borrow()
            .iter()
            .map(|call| {
                accumulated.push_str(&call.content);
                Self::strip_ansi(&accumulated)
                    .replace('\r', "")
                    .replace('\n', " ")
                    .trim()
                    .to_string()
            })
            .filter(|s| !s.is_empty())
            .collect()
    }

    /// Clear all recorded write and flush calls.
    pub fn clear(&self) {
        self.write_calls.borrow_mut().clear();
        self.flush_calls.borrow_mut().clear();
    }

    /// Get all flush calls for inspection.
    pub fn get_flush_calls(&self) -> Vec<FlushCall> {
        self.flush_calls.borrow().clone()
    }

    /// Get the number of `flush()` calls made.
    pub fn flush_count(&self) -> usize {
        self.flush_calls.borrow().len()
    }

    /// Verify that flush was called after writes occurred.
    ///
    /// This is the critical test for real-time streaming behavior:
    /// if flush isn't called, output buffers and appears "all at once".
    ///
    /// # Returns
    /// `Ok(())` if at least one flush occurred after writes, `Err` with details otherwise.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn verify_flush_after_writes(&self) -> Result<(), String> {
        let writes = self.write_calls.borrow();
        let flushes = self.flush_calls.borrow();

        if writes.is_empty() {
            return Err("No writes occurred".to_string());
        }

        if flushes.is_empty() {
            return Err(format!(
                "No flush() calls occurred after {} write(s). \
                 This means output is buffered and will appear 'all at once' \
                 instead of streaming incrementally.",
                writes.len()
            ));
        }

        Ok(())
    }

    /// Verify that flush was called at least `min_expected` times.
    ///
    /// For true streaming, flush should be called after each delta event
    /// to push content to the user's terminal immediately.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn verify_flush_count(&self, min_expected: usize) -> Result<(), String> {
        let count = self.flush_count();
        if count >= min_expected {
            Ok(())
        } else {
            Err(format!(
                "Expected at least {min_expected} flush() calls, but only {count} occurred. \
                 This suggests output is not being flushed frequently enough for streaming."
            ))
        }
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Default for StreamingTestPrinter {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl std::io::Write for StreamingTestPrinter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let content =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;

        self.write_calls.borrow_mut().push(WriteCall {
            content: content.to_string(),
            timestamp: std::time::Instant::now(),
        });

        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        let last_write_index = if self.write_calls.borrow().is_empty() {
            None
        } else {
            Some(self.write_calls.borrow().len() - 1)
        };
        self.flush_calls.borrow_mut().push(FlushCall {
            last_write_index,
            timestamp: std::time::Instant::now(),
        });
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for StreamingTestPrinter {
    fn is_terminal(&self) -> bool {
        self.simulated_is_terminal
    }
}
