// Test printer implementation.
//
// Contains the TestPrinter for capturing output in tests.

/// Test printer that captures output for assertion.
///
/// This printer stores all output in memory for testing purposes.
/// It provides methods to retrieve and inspect the captured output.
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Default, Clone)]
pub struct TestPrinter {
    output: Vec<String>,
    buffer: String,
}

#[cfg(any(test, feature = "test-utils"))]
impl TestPrinter {
    /// Create a new test printer.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Get all captured output as a single string.
    #[must_use]
    pub fn get_output(&self) -> String {
        let mut result = self.buffer.clone();
        for line in self.output.iter() {
            result.push_str(line);
        }
        result
    }

    /// Get captured output lines.
    #[must_use]
    pub fn get_lines(&self) -> Vec<String> {
        let mut result: Vec<String> = self.output.clone();
        if !self.buffer.is_empty() {
            result.push(self.buffer.clone());
        }
        result
    }

    /// Clear all captured output.
    #[must_use]
    pub fn clear(self) -> Self {
        Self {
            output: Vec::new(),
            buffer: String::new(),
        }
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

    /// Write text to the buffer (functional style).
    #[must_use]
    pub fn write_text(self, text: &str) -> Self {
        let mut new_buffer = self.buffer.clone();
        new_buffer.push_str(text);

        let mut new_output = self.output.clone();
        while let Some(newline_pos) = new_buffer.find('\n') {
            let line = new_buffer.drain(..=newline_pos).collect::<String>();
            new_output.push(line);
        }

        Self {
            output: new_output,
            buffer: new_buffer,
        }
    }

    /// Write text with raw write (for std::io::Write compatibility).
    fn write_raw(&mut self, buf: &[u8]) -> io::Result<()> {
        let s =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        self.buffer.push_str(s);

        while let Some(newline_pos) = self.buffer.find('\n') {
            let line = self.buffer.drain(..=newline_pos).collect::<String>();
            self.output.push(line);
        }
        Ok(())
    }

    /// Flush the buffer (functional style).
    #[must_use]
    pub fn flush(self) -> Self {
        let mut new_output = self.output;
        if !self.buffer.is_empty() {
            new_output.push(self.buffer.clone());
        }
        Self {
            output: new_output,
            buffer: String::new(),
        }
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl std::io::Write for TestPrinter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.write_raw(buf)?;
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        if !self.buffer.is_empty() {
            self.output.push(self.buffer.clone());
            self.buffer.clear();
        }
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for TestPrinter {
    fn is_terminal(&self) -> bool {
        false
    }
}
