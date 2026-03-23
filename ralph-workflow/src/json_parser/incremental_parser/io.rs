// Boundary module for IncrementalNdjsonParser byte processing.
//
// The byte-by-byte state machine requires in-place mutation of the buffer
// for O(1) amortized appends. This boundary module is exempt from
// forbid_mut_binding and forbid_mutating_receiver_methods.

impl IncrementalNdjsonParser {
    fn handle_open_brace(mut self) -> Self {
        if self.depth + 1 > MAX_JSON_DEPTH {
            self.buffer.clear();
            self.depth = 0;
            self.started = false;
            self.in_string = false;
            self.escape_next = false;
        } else {
            self.buffer.push(b'{');
            self.depth = self.depth.saturating_add(1);
            self.started = true;
        }
        self
    }

    fn finalize_json_object(mut self) -> Self {
        let buffer = std::mem::take(&mut self.buffer);
        if let Ok(json_str) = String::from_utf8(buffer) {
            let trimmed = json_str.trim();
            if !trimmed.is_empty() { self.results.push(trimmed.to_string()); }
        }
        self.started = false;
        self
    }

    fn handle_close_brace(mut self) -> Self {
        self.buffer.push(b'}');
        self.depth = self.depth.saturating_sub(1);
        if self.depth == 0 { return self.finalize_json_object(); }
        self
    }

    fn process_quote(mut self) -> Self {
        self.buffer.push(b'"');
        if self.started { self.in_string = !self.in_string; }
        self
    }

    pub(super) fn process_byte(mut self, byte: u8) -> Self {
        if !self.started && byte != b'{' { return self; }
        if self.escape_next { self.buffer.push(byte); self.escape_next = false; return self; }
        if byte == b'\\' && self.in_string { self.buffer.push(byte); self.escape_next = true; return self; }
        if byte == b'"' { return self.process_quote(); }
        if byte == b'{' && !self.in_string { return self.handle_open_brace(); }
        if byte == b'}' && !self.in_string && self.started { return self.handle_close_brace(); }
        self.buffer.push(byte);
        self
    }
}
