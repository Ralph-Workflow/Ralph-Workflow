//! ANSI escape sequence processing for `VirtualTerminal`.
//!
//! This module handles parsing and interpretation of ANSI escape sequences
//! for cursor movement, line clearing, and text styling.

use super::VirtualTerminal;

#[cfg(any(test, feature = "test-utils"))]
impl VirtualTerminal {
    /// Process a string, interpreting control characters and ANSI sequences.
    pub(super) fn process_string(&self, s: &str) {
        let mut chars = s.chars().peekable();
        let mut text_buffer = String::new();

        // Flush accumulated text to the terminal
        let flush_text = |term: &Self, buf: &mut String| {
            if !buf.is_empty() {
                term.write_str(buf);
                buf.clear();
            }
        };

        while let Some(c) = chars.next() {
            match c {
                '\r' => {
                    flush_text(self, &mut text_buffer);
                    // Carriage return: move to column 0
                    *self.cursor_col.borrow_mut() = 0;
                }
                '\n' => {
                    flush_text(self, &mut text_buffer);
                    // Newline: move to next row, column 0
                    let new_row = self.cursor_row.borrow().saturating_add(1);
                    *self.cursor_row.borrow_mut() = new_row;
                    *self.cursor_col.borrow_mut() = 0;
                    self.ensure_row_exists();
                }
                '\x1b' => {
                    flush_text(self, &mut text_buffer);
                    // ANSI escape sequence
                    if chars.peek() == Some(&'[') {
                        chars.next(); // consume '['

                        // Parse the numeric parameter (if any)
                        let mut param = String::new();
                        while let Some(&c) = chars.peek() {
                            if c.is_ascii_digit() {
                                param.push(c);
                                chars.next();
                            } else {
                                break;
                            }
                        }

                        // Get the command character
                        if let Some(cmd) = chars.next() {
                            let n: usize = param.parse().unwrap_or(1);
                            match cmd {
                                'A' => self.cursor_up(n),   // Cursor up
                                'B' => self.cursor_down(n), // Cursor down
                                'K' => {
                                    // Erase in line
                                    // \x1b[K or \x1b[0K - erase from cursor to end
                                    // \x1b[1K - erase from start to cursor
                                    // \x1b[2K - erase entire line
                                    let mode: usize = param.parse().unwrap_or(0);
                                    if mode == 2 {
                                        self.clear_line();
                                    }
                                    // For now, we only implement mode 2 (full line clear)
                                    // which is what the streaming code uses
                                }
                                _ => {
                                    // SGR (Select Graphic Rendition) - colors/styles, or unknown command
                                    // We ignore these as they don't affect text content
                                }
                            }
                        }
                    }
                }
                _ => {
                    // Regular character: buffer it for batch writing
                    text_buffer.push(c);
                }
            }
        }

        // Flush any remaining text
        flush_text(self, &mut text_buffer);
    }
}
