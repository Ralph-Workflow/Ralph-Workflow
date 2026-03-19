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
/// let (parser, events1) = parser.feed_and_get_events(b"{\"type\": \"de");
/// assert_eq!(events1.len(), 0);  // Not complete yet
///
/// // Feed second half
/// let (_, events2) = parser.feed_and_get_events(b"lta\"}\n");
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
    /// Complete JSON objects extracted so far
    results: Vec<String>,
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
            results: Vec::new(),
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
    /// A tuple of (updated parser, vector of complete JSON strings), in the order they were completed.
    pub fn feed(mut self, byte: u8) -> Self {
        self.process_byte(byte);
        self
    }

    pub fn feed_and_get_events(mut self, data: &[u8]) -> (Self, Vec<String>) {
        data.iter().for_each(|&byte| {
            self.process_byte(byte);
        });
        let results = self.results.drain(..).collect();
        (self, results)
    }

    pub fn drain_results(&mut self) -> Vec<String> {
        self.results.drain(..).collect()
    }

    fn process_byte(&mut self, byte: u8) {
        if !self.started && byte != b'{' {
            return;
        }

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
                if self.depth + 1 > MAX_JSON_DEPTH {
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

                if self.depth == 0 {
                    self.extract_complete_json();
                }
            }
            _ => {
                self.buffer.push(byte);
            }
        }
    }

    fn extract_complete_json(&mut self) {
        let json_end = self.buffer.len();

        if let Ok(json_str) = String::from_utf8(self.buffer.drain(..json_end).collect()) {
            let trimmed = json_str.trim();
            if !trimmed.is_empty() {
                self.results.push(trimmed.to_string());
            }
        }

        self.started = false;
    }

    /// Get any complete JSON objects extracted so far.
    #[must_use]
    pub fn get_results(&self) -> Vec<String> {
        self.results.clone()
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
    /// let (_, _) = parser.feed_and_get_events(b"{\"type\": \"delta\"}\n{\"type\": \"incomplete\"");
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
        let parser = IncrementalNdjsonParser::new();
        let (_, events) = parser.feed_and_get_events(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_split_json() {
        let parser = IncrementalNdjsonParser::new();

        let (parser, events1) = parser.feed_and_get_events(b"{\"type\": \"de");
        assert_eq!(events1.len(), 0);

        let (_, events2) = parser.feed_and_get_events(b"lta\"}\n");
        assert_eq!(events2.len(), 1);
        assert_eq!(events2[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_multiple_jsons() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"{\"type\": \"delta\"}\n{\"type\": \"done\"}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], "{\"type\": \"delta\"}");
        assert_eq!(events[1], "{\"type\": \"done\"}");
    }

    #[test]
    fn test_incremental_parser_nested_json() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"{\"type\": \"delta\", \"data\": {\"nested\": true}}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\"nested\": true"));
    }

    #[test]
    fn test_incremental_parser_json_with_strings_containing_braces() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"{\"text\": \"hello {world}\"}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"text\": \"hello {world}\"}");
    }

    #[test]
    fn test_incremental_parser_json_with_escaped_quotes() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"{\"text\": \"hello \\\"world\\\"\"}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\\\""));
    }

    #[test]
    fn test_incremental_parser_empty_input() {
        let parser = IncrementalNdjsonParser::new();
        let (_, events) = parser.feed_and_get_events(b"");
        assert_eq!(events.len(), 0);
    }

    #[test]
    fn test_incremental_parser_whitespace_only() {
        let parser = IncrementalNdjsonParser::new();
        let (_, events) = parser.feed_and_get_events(b"   \n  \n");
        assert_eq!(events.len(), 0);
    }

    #[test]
    fn test_incremental_parser_ignores_preamble_before_json() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"[i] Joined existing CLIProxy\n{\"type\":\"delta\"}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events, vec!["{\"type\":\"delta\"}".to_string()]);
    }

    #[test]
    fn test_incremental_parser_clear() {
        let parser = IncrementalNdjsonParser::new();

        let (mut parser, _) = parser.feed_and_get_events(b"{\"type\":");
        assert!(parser.is_parsing());

        parser.clear();
        assert!(!parser.is_parsing());

        let (_, events) = parser.feed_and_get_events(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn test_incremental_parser_byte_by_byte() {
        let input = b"{\"type\": \"delta\"}\n";
        let all_events: Vec<String> = input
            .iter()
            .map(|&b| IncrementalNdjsonParser::new().feed(b))
            .flat_map(|parser| parser.drain_results())
            .collect();

        assert_eq!(all_events.len(), 1);
        assert_eq!(all_events[0], "{\"type\": \"delta\"}");
    }

    #[test]
    fn test_incremental_parser_multiline_json() {
        let parser = IncrementalNdjsonParser::new();
        let input = b"{\n  \"type\": \"delta\",\n  \"value\": 123\n}\n";
        let (_, events) = parser.feed_and_get_events(input);
        assert_eq!(events.len(), 1);
        assert!(events[0].contains("\"type\": \"delta\""));
        assert!(events[0].contains("\"value\": 123"));
    }

    #[test]
    fn test_incremental_parser_depth_limit() {
        let input = "{".repeat(MAX_JSON_DEPTH + 1);
        let (parser, events) = IncrementalNdjsonParser::new().feed_and_get_events(input.as_bytes());
        assert_eq!(events.len(), 0);
        assert!(!parser.is_parsing());
    }

    #[test]
    fn test_incremental_parser_finish_returns_buffered_data() {
        let parser = IncrementalNdjsonParser::new();
        let (parser, events) = parser.feed_and_get_events(b"{\"type\": \"incomplete\"");
        assert_eq!(events, vec![] as Vec<String>);

        let remaining = parser.finish();
        assert_eq!(remaining, Some("{\"type\": \"incomplete\"".to_string()));
    }

    #[test]
    fn test_incremental_parser_finish_returns_none_for_empty_buffer() {
        let parser = IncrementalNdjsonParser::new();
        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_returns_none_for_complete_json() {
        let parser = IncrementalNdjsonParser::new();
        let (parser, events) = parser.feed_and_get_events(b"{\"type\": \"delta\"}\n");
        assert_eq!(events.len(), 1);

        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_with_complete_json_no_newline() {
        let parser = IncrementalNdjsonParser::new();
        let (parser, events) = parser.feed_and_get_events(b"{\"type\": \"delta\"}");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0], "{\"type\": \"delta\"}");

        assert_eq!(parser.finish(), None);
    }

    #[test]
    fn test_incremental_parser_finish_with_incomplete_json_missing_brace() {
        let parser = IncrementalNdjsonParser::new();
        let (parser, events) = parser.feed_and_get_events(b"{\"type\": \"delta\"");
        assert_eq!(events.len(), 0);

        let remaining = parser.finish();
        assert_eq!(remaining, Some("{\"type\": \"delta\"".to_string()));
    }
}
