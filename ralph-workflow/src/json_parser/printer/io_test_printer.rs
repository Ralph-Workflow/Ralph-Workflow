// Test printer implementation.
//
// Contains the TestPrinter for capturing output in tests.

/// Test printer that captures output for assertion.
///
/// This printer stores all output in memory for testing purposes.
/// It provides methods to retrieve and inspect the captured output.
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Default)]
pub struct TestPrinter {
    /// Captured output lines.
    output: RefCell<Vec<String>>,
    /// Buffer for incomplete lines.
    buffer: RefCell<String>,
}

#[cfg(any(test, feature = "test-utils"))]
impl TestPrinter {
    /// Create a new test printer.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Get all captured output as a single string.
    pub fn get_output(&self) -> String {
        let mut result = self.buffer.borrow().clone();
        for line in self.output.borrow().iter() {
            result.push_str(line);
        }
        result
    }

    /// Get captured output lines.
    pub fn get_lines(&self) -> Vec<String> {
        let mut result: Vec<String> = self.output.borrow().clone();
        let buffer = self.buffer.borrow();
        if !buffer.is_empty() {
            result.push(buffer.clone());
        }
        result
    }

    /// Clear all captured output.
    pub fn clear(&self) {
        self.output.borrow_mut().clear();
        self.buffer.borrow_mut().clear();
    }

    /// Check if a specific line exists in the output.
    pub fn has_line(&self, line: &str) -> bool {
        self.get_lines().iter().any(|l| l.contains(line))
    }

    /// Get the number of times a specific pattern appears in output.
    pub fn count_pattern(&self, pattern: &str) -> usize {
        self.get_lines()
            .iter()
            .filter(|l| l.contains(pattern))
            .count()
    }

    /// Check if there are duplicate consecutive lines in output.
    pub fn has_duplicate_consecutive_lines(&self) -> bool {
        let lines = self.get_lines();
        for i in 1..lines.len() {
            if lines[i] == lines[i - 1] && !lines[i].is_empty() {
                return true;
            }
        }
        false
    }

    /// Find and return all duplicate consecutive lines.
    pub fn find_duplicate_consecutive_lines(&self) -> Vec<(usize, String)> {
        let mut duplicates = Vec::new();
        let lines = self.get_lines();
        for i in 1..lines.len() {
            if lines[i] == lines[i - 1] && !lines[i].is_empty() {
                duplicates.push((i - 1, lines[i - 1].clone()));
            }
        }
        duplicates
    }

    /// Get statistics about the output.
    ///
    /// Returns a tuple of (`line_count`, `char_count`).
    pub fn get_stats(&self) -> (usize, usize) {
        let lines = self.get_lines();
        let char_count: usize = lines.iter().map(String::len).sum();
        (lines.len(), char_count)
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl std::io::Write for TestPrinter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let s =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        let mut buffer = self.buffer.borrow_mut();
        buffer.push_str(s);

        // Process complete lines
        while let Some(newline_pos) = buffer.find('\n') {
            let line = buffer.drain(..=newline_pos).collect::<String>();
            self.output.borrow_mut().push(line);
        }

        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        // Flush any remaining buffer content
        let mut buffer = self.buffer.borrow_mut();
        if !buffer.is_empty() {
            self.output.borrow_mut().push(buffer.clone());
            buffer.clear();
        }
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for TestPrinter {
    fn is_terminal(&self) -> bool {
        // Test printer is never a terminal
        false
    }
}
