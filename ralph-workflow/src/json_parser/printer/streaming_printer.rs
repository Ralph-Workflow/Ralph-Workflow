// Streaming test printer implementation.
//
// Contains StreamingTestPrinter for capturing individual write calls.

#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone)]
pub struct WriteCall {
    pub content: String,
    pub timestamp: std::time::Instant,
}

#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug, Clone)]
pub struct FlushCall {
    pub last_write_index: Option<usize>,
    pub timestamp: std::time::Instant,
}

#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug)]
pub struct StreamingTestPrinter {
    write_calls: Vec<WriteCall>,
    flush_calls: Vec<FlushCall>,
    simulated_is_terminal: bool,
}

#[cfg(any(test, feature = "test-utils"))]
impl StreamingTestPrinter {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            write_calls: Vec::new(),
            flush_calls: Vec::new(),
            simulated_is_terminal: false,
        }
    }

    #[must_use]
    pub const fn new_with_terminal(is_terminal: bool) -> Self {
        Self {
            write_calls: Vec::new(),
            flush_calls: Vec::new(),
            simulated_is_terminal: is_terminal,
        }
    }

    pub fn get_write_calls(&self) -> Vec<WriteCall> {
        self.write_calls.clone()
    }

    pub fn write_count(&self) -> usize {
        self.write_calls.len()
    }

    pub fn get_full_output(&self) -> String {
        self.write_calls.iter().map(|w| w.content.clone()).collect()
    }

    pub fn get_content_at_write(&self, index: usize) -> Option<String> {
        self.write_calls.get(index).map(|w| w.content.clone())
    }

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

    pub fn contains_escape_sequence(&self, seq: &str) -> bool {
        self.get_full_output().contains(seq)
    }

    pub fn has_any_escape_sequences(&self) -> bool {
        self.get_full_output().contains('\x1b')
    }

    #[must_use]
    pub fn strip_ansi(s: &str) -> String {
        s.chars()
            .fold((String::new(), false), |(mut result, in_esc), c| {
                if c == '\x1b' {
                    (result, true)
                } else if in_esc {
                    if !c.is_ascii_alphabetic() {
                        (result, true)
                    } else {
                        (result, false)
                    }
                } else {
                    result.push(c);
                    (result, false)
                }
            })
            .0
    }

    pub fn get_content_progression(&self) -> Vec<String> {
        self.write_calls
            .iter()
            .scan(String::new(), |accumulated, call| {
                accumulated.push_str(&call.content);
                Some(
                    Self::strip_ansi(accumulated)
                        .replace('\r', "")
                        .replace('\n', " ")
                        .trim()
                        .to_string(),
                )
            })
            .filter(|s| !s.is_empty())
            .collect()
    }

    #[must_use]
    pub fn clear(self) -> Self {
        Self {
            write_calls: Vec::new(),
            flush_calls: Vec::new(),
            simulated_is_terminal: self.simulated_is_terminal,
        }
    }

    pub fn get_flush_calls(&self) -> Vec<FlushCall> {
        self.flush_calls.clone()
    }

    pub fn flush_count(&self) -> usize {
        self.flush_calls.len()
    }

    pub fn verify_flush_after_writes(&self) -> Result<(), String> {
        if self.write_calls.is_empty() {
            return Err("No writes occurred".to_string());
        }

        if self.flush_calls.is_empty() {
            return Err(format!(
                "No flush() calls occurred after {} write(s). \
                 This means output is buffered and will appear 'all at once' \
                 instead of streaming incrementally.",
                self.write_calls.len()
            ));
        }

        Ok(())
    }

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

    /// Write text (functional style).
    #[must_use]
    pub fn write_text(self, content: &str) -> Self {
        Self {
            write_calls: self
                .write_calls
                .iter()
                .cloned()
                .chain(std::iter::once(WriteCall {
                    content: content.to_string(),
                    timestamp: std::time::Instant::now(),
                }))
                .collect(),
            flush_calls: self.flush_calls,
            simulated_is_terminal: self.simulated_is_terminal,
        }
    }

    /// Flush (functional style).
    #[must_use]
    pub fn flush_call(self) -> Self {
        let last_write_index = if self.write_calls.is_empty() {
            None
        } else {
            Some(self.write_calls.len() - 1)
        };
        Self {
            write_calls: self.write_calls,
            flush_calls: self
                .flush_calls
                .iter()
                .cloned()
                .chain(std::iter::once(FlushCall {
                    last_write_index,
                    timestamp: std::time::Instant::now(),
                }))
                .collect(),
            simulated_is_terminal: self.simulated_is_terminal,
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

        self.write_calls.push(WriteCall {
            content: content.to_string(),
            timestamp: std::time::Instant::now(),
        });

        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        let last_write_index = if self.write_calls.is_empty() {
            None
        } else {
            Some(self.write_calls.len() - 1)
        };
        self.flush_calls.push(FlushCall {
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
