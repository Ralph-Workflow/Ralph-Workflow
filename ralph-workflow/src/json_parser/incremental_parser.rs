//! Incremental NDJSON parser for real-time streaming.
//!
//! This module provides a parser that can process NDJSON (newline-delimited JSON)
//! incrementally, yielding complete JSON objects as soon as they're received,
//! without waiting for newlines.
//!
//! # Why Incremental Parsing?
//!
//! The standard approach of using `reader.lines()` blocks until a newline is received.
//! For AI agents that buffer their output (like Codex), this causes all output to appear
//! at once instead of streaming character-by-character.
//!
//! This parser detects complete JSON objects by tracking brace nesting depth,
//! allowing true real-time streaming like `ChatGPT`.

/// Incremental NDJSON parser that yields complete JSON objects as they arrive.
///
/// # How It Works
///
/// The parser maintains a buffer of received bytes and tracks brace nesting depth.
/// When the depth returns to zero after seeing a closing brace, we have a complete
/// JSON object that can be parsed.
///
/// # Depth Limit
///
/// The parser enforces a maximum nesting depth to prevent integer overflow
/// from malicious input with extremely deep nesting. If the depth exceeds
/// this limit, parsing fails with an error.
///
/// # Example
///
/// ```ignore
/// let mut parser = IncrementalNdjsonParser::new();
///
/// // Feed first half of JSON
/// let events1 = parser.feed(b"{\"type\": \"de");
/// assert_eq!(events1.len(), 0);  // Not complete yet
///
/// // Feed second half
/// let events2 = parser.feed(b"lta\"}\n");
/// assert_eq!(events2.len(), 1);  // Complete!
/// assert_eq!(events2[0], "{\"type\": \"delta\"}");
/// ```
pub struct IncrementalNdjsonParser {
    /// Buffer of received bytes that haven't been parsed yet
    buffer: Vec<u8>,
    /// Current brace nesting depth (0 means top-level)
    depth: usize,
    /// Whether we're inside a string literal
    in_string: bool,
    /// Whether the next character is escaped
    escape_next: bool,
    /// Whether we've seen at least one opening brace (started parsing JSON)
    started: bool,
}

/// Maximum allowed nesting depth for JSON objects.
/// This prevents integer overflow from malicious input with extremely deep nesting.
/// Most well-formed JSON has nesting depth < 20, so 1000 is a conservative limit.
const MAX_JSON_DEPTH: usize = 1000;

impl IncrementalNdjsonParser {
    /// Create a new incremental NDJSON parser.
    pub const fn new() -> Self {
        Self {
            buffer: Vec::new(),
            depth: 0,
            in_string: false,
            escape_next: false,
            started: false,
        }
    }

    /// Feed bytes into the parser, returning any complete JSON objects found.
    ///
    /// This method processes the input bytes and extracts complete JSON objects.
    /// Multiple JSON objects may be returned from a single call if they're all complete.
    ///
    /// # Arguments
    ///
    /// * `data` - Bytes to feed into the parser
    ///
    /// # Returns
    ///
    /// A vector of complete JSON strings, in the order they were completed.
    pub fn feed(&mut self, data: &[u8]) -> Vec<String> {
        let mut complete_jsons = Vec::new();

        for &byte in data {
            self.process_byte(byte, &mut complete_jsons);
        }

        complete_jsons
    }

    /// Process a single byte, tracking state and extracting complete JSONs.
    ///
    /// If the depth exceeds `MAX_JSON_DEPTH`, the parser will reset to a safe
    /// state and skip the current JSON to prevent integer overflow from malicious input.
    fn process_byte(&mut self, byte: u8, complete_jsons: &mut Vec<String>) {
        // Ignore any non-JSON preamble before the first opening brace.
        //
        // Some real-world streams start with log lines or other text before the first JSON
        // object. We only start buffering once we see the first `{`.
        if !self.started && byte != b'{' {
            return;
        }

        // Handle escape sequences
        if self.escape_next {
            self.buffer.push(byte);
            self.escape_next = false;
            return;
        }

        match byte {
            b'\\' if self.in_string => {
                self.buffer.push(byte);
                self.escape_next = true;
            }
            b'"' => {
                self.buffer.push(byte);
                if self.started {
                    self.in_string = !self.in_string;
                }
            }
            b'{' if !self.in_string => {
                // Check for depth limit to prevent overflow BEFORE pushing
                if self.depth + 1 > MAX_JSON_DEPTH {
                    // Depth exceeded - reset parser state to skip this malformed JSON
                    self.buffer.clear();
                    self.depth = 0;
                    self.started = false;
                    self.in_string = false;
                    self.escape_next = false;
                } else {
                    self.buffer.push(byte);
                    self.depth = self.depth.saturating_add(1);
                    self.started = true;
                }
            }
            b'}' if !self.in_string && self.started => {
                self.buffer.push(byte);
                self.depth = self.depth.saturating_sub(1);

                // When depth returns to 0, we have a complete JSON object
                if self.depth == 0 {
                    self.extract_complete_json(complete_jsons);
                }
            }
            _ => {
                self.buffer.push(byte);
            }
        }
    }

    /// Extract a complete JSON object from the buffer.
    fn extract_complete_json(&mut self, complete_jsons: &mut Vec<String>) {
        // Find the end of the JSON (we know it's complete at this point)
        // Look for the closing brace that brought us to depth 0
        let json_end = self.buffer.len();

        // Convert to UTF-8 (should be valid JSON)
        let Ok(json_str) = String::from_utf8(self.buffer.drain(..json_end).collect()) else {
            // Invalid UTF-8 - skip this JSON silently
            self.started = false;
            return;
        };

        // Trim whitespace and check if it's non-empty
        let trimmed = json_str.trim();
        if !trimmed.is_empty() {
            complete_jsons.push(trimmed.to_string());
        }

        // Reset state for next JSON
        self.started = false;
    }

    /// Get any incomplete JSON currently in the buffer.
    ///
    /// This method is available in test builds for debugging purposes.
    /// It allows inspection of partial JSON data during parser development.
    #[must_use]
    #[cfg(test)]
    pub fn partial(&self) -> &[u8] {
        &self.buffer
    }

    /// Clear the internal buffer.
    ///
    /// This can be useful for error recovery when invalid data is encountered.
    #[cfg(test)]
    pub fn clear(&mut self) {
        self.buffer.clear();
        self.depth = 0;
        self.in_string = false;
        self.escape_next = false;
        self.started = false;
    }

    /// Check if the parser is currently inside a JSON object.
    #[must_use]
    #[cfg(test)]
    pub const fn is_parsing(&self) -> bool {
        self.started
    }

    /// Finalize parsing and return any remaining buffered data.
    ///
    /// This method should be called when the input stream ends to retrieve
    /// any incomplete JSON that was buffered. This is important for handling
    /// cases where the last line of a file doesn't have a trailing newline
    /// or where a complete JSON object was received but not yet extracted.
    ///
    /// # Returns
    ///
    /// Any remaining buffered data as a string if non-empty, or None if buffer is empty.
    ///
    /// # Example
    ///
    /// ```ignore
    /// let mut parser = IncrementalNdjsonParser::new();
    /// parser.feed(b"{\"type\": \"delta\"}\n{\"type\": \"incomplete\"");
    /// // When stream ends, get any remaining buffered data
    /// if let Some(remaining) = parser.finish() {
    ///     println!("Remaining: {}", remaining);
    /// }
    /// ```
    #[must_use]
    pub fn finish(mut self) -> Option<String> {
        let trimmed = String::from_utf8(self.buffer.drain(..).collect())
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());

        // Reset state to clean state
        self.buffer.clear();
        self.depth = 0;
        self.in_string = false;
        self.escape_next = false;
        self.started = false;

        trimmed
    }
}

impl Default for IncrementalNdjsonParser {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_incremental_parser_single_json() {
        let mut parser = IncrementalNdjsonParser::new();
        let events = parser.feed(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_split_json() {
        let mut parser = IncrementalNdjsonParser::new();

        // Feed first half
        let events1 = parser.feed(b"{\"type\": \"de");
        assert_eq!(events1.len(), 0);

        // Feed second half
        let events2 = parser.feed(b"lta\"}\n");
        assert_eq!(events2.len(), 1);
        assert_eq!(events2[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_multiple_jsons() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\"type\": \"delta\"}\n{\"type\": \"done\"}\n";
        let events = parser.feed(input);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], "{\"type\": \"delta\"}");
        assert_eq!(events[1], "{\"type\": \"done\"}");
    }

    #[test]
    fn test_incremental_parser_nested_json() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\"type\": \"delta\", \"data\": {\"nested\": true}}\n";
        let events = parser.feed(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\"nested\": true"));
    }

    #[test]
    fn test_incremental_parser_json_with_strings_containing_braces() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\"text\": \"hello {world}\"}\n";
        let events = parser.feed(input);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"text\": \"hello {world}\"}");
    }

    #[test]
    fn test_incremental_parser_json_with_escaped_quotes() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\"text\": \"hello \\\"world\\\"\"}\n";
        let events = parser.feed(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\\\""));
    }

    #[test]
    fn test_incremental_parser_empty_input() {
        let mut parser = IncrementalNdjsonParser::new();
        let events = parser.feed(b"");
        assert_eq!(events.len(), 0);
    }

    #[test]
    fn test_incremental_parser_whitespace_only() {
        let mut parser = IncrementalNdjsonParser::new();
        let events = parser.feed(b"   \n  \n");
        assert_eq!(events.len(), 0);
    }

    #[test]
    fn test_incremental_parser_ignores_preamble_before_json() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"[i] Joined existing CLIProxy\n{\"type\":\"delta\"}\n";
        let events = parser.feed(input);
        assert_eq!(events, vec!["{\"type\":\"delta\"}".to_string()]);
    }

    #[test]
    fn test_incremental_parser_clear() {
        let mut parser = IncrementalNdjsonParser::new();

        // Feed incomplete JSON
        parser.feed(b"{\"type\":");
        assert!(parser.is_parsing());

        // Clear
        parser.clear();
        assert!(!parser.is_parsing());
        assert_eq!(parser.partial().len(), 0);

        // Should be able to parse new JSON
        let events = parser.feed(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn test_incremental_parser_byte_by_byte() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\"type\": \"delta\"}\n";

        let mut events = Vec::new();
        for byte in input {
            events.extend(parser.feed(&[*byte]));
        }

        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_multiline_json() {
        let mut parser = IncrementalNdjsonParser::new();
        let input = b"{\n  \"type\": \"delta\",\n  \"value\": 123\n}\n";
        let events = parser.feed(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\"type\": \"delta\""));
        assert!(events[0].contains("\"value\": 123"));
    }

    #[test]
    fn test_incremental_parser_depth_limit() {
        let mut parser = IncrementalNdjsonParser::new();
        // Create JSON with depth exceeding MAX_JSON_DEPTH.
        // We need exactly MAX_JSON_DEPTH + 1 opening braces to exceed the limit.
        let mut input = String::new();
        for _ in 0..=MAX_JSON_DEPTH {
            input.push('{');
        }
        // Feed the deeply nested input - should handle gracefully without panicking
        let events = parser.feed(input.as_bytes());
        // Parser should reset and return no events (skipped malformed JSON)
        assert_eq!(events.len(), 0);
        // Parser should be in a clean state after handling the error
        assert!(!parser.is_parsing());
        assert_eq!(parser.partial().len(), 0);
    }

    #[test]
    fn test_incremental_parser_finish_returns_buffered_data() {
        let mut parser = IncrementalNdjsonParser::new();
        // Feed incomplete JSON (missing closing brace)
        // Note: `b"{\"type\": \"incomplete\""` is the byte string for `{"type": "incomplete"` (no closing brace)
        let events = parser.feed(b"{\"type\": \"incomplete\"");
        // No events should be returned yet (missing closing brace)
        assert_eq!(events, vec![] as Vec<String>);

        // finish() should return the buffered data
        let remaining = parser.finish();
        // Note: The buffered data is the incomplete JSON string we fed
        assert_eq!(remaining, Some("{\"type\": \"incomplete\"".to_string()));
    }

    #[test]
    fn test_incremental_parser_finish_returns_none_for_empty_buffer() {
        let parser = IncrementalNdjsonParser::new();
        // No data fed, finish should return None
        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_returns_none_for_complete_json() {
        let mut parser = IncrementalNdjsonParser::new();
        // Feed complete JSON
        let events = parser.feed(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);

        // Buffer should be empty, so finish() should return None
        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_with_complete_json_no_newline() {
        let mut parser = IncrementalNdjsonParser::new();
        // Feed complete JSON but without trailing newline
        // The parser DOES extract it when closing brace is encountered (depth 0)
        let events = parser.feed(b"{\"type\": \"delta\"}");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"type\": \"delta\"}");

        // Buffer should be empty after extraction, so finish() should return None
        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_with_incomplete_json_missing_brace() {
        let mut parser = IncrementalNdjsonParser::new();
        // Feed complete JSON but missing the closing brace
        let events = parser.feed(b"{\"type\": \"delta\"");
        assert_eq!(events.len(), 0); // Not complete yet

        // finish() should return the buffered incomplete JSON
        let remaining = parser.finish();
        assert_eq!(remaining, Some("{\"type\": \"delta\"".to_string()));
    }
}
