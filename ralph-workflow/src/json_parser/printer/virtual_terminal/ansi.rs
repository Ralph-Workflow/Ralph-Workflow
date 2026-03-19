//! ANSI escape sequence processing for `VirtualTerminal`.
//!
//! This module handles parsing and interpretation of ANSI escape sequences
//! for cursor movement, line clearing, and text styling.

use super::VirtualTerminal;

#[cfg(any(test, feature = "test-utils"))]
impl VirtualTerminal {
    /// Process a string, interpreting control characters and ANSI sequences.
    pub(super) fn process_string(&self, s: &str) {
        let chars: Vec<char> = s.chars().collect();
        let mut i = 0;
        let mut text_buffer = String::new();

        let flush_text = |term: &Self, buf: &mut String| {
            if !buf.is_empty() {
                term.write_str(buf);
                buf.clear();
            }
        };

        while i < chars.len() {
            let c = chars[i];
            match c {
                '\r' => {
                    flush_text(self, &mut text_buffer);
                    *self.cursor_col.borrow_mut() = 0;
                    i += 1;
                }
                '\n' => {
                    flush_text(self, &mut text_buffer);
                    let new_row = self.cursor_row.borrow().saturating_add(1);
                    *self.cursor_row.borrow_mut() = new_row;
                    *self.cursor_col.borrow_mut() = 0;
                    self.ensure_row_exists();
                    i += 1;
                }
                '\x1b' => {
                    flush_text(self, &mut text_buffer);
                    if i + 1 < chars.len() && chars[i + 1] == '[' {
                        i += 2;

                        let mut param_end = i;
                        while param_end < chars.len() && chars[param_end].is_ascii_digit() {
                            param_end += 1;
                        }
                        let param: String = chars[i..param_end].iter().collect();
                        i = param_end;

                        if i < chars.len() {
                            let cmd = chars[i];
                            let n: usize = param.parse().unwrap_or(1);
                            match cmd {
                                'A' => {
                                    self.cursor_up(n);
                                    i += 1;
                                }
                                'B' => {
                                    self.cursor_down(n);
                                    i += 1;
                                }
                                'K' => {
                                    let mode: usize = param.parse().unwrap_or(0);
                                    if mode == 2 {
                                        self.clear_line();
                                    }
                                    i += 1;
                                }
                                _ => {
                                    i += 1;
                                }
                            }
                        }
                    } else {
                        i += 1;
                    }
                }
                _ => {
                    text_buffer.push(c);
                    i += 1;
                }
            }
        }

        flush_text(self, &mut text_buffer);
    }
}
