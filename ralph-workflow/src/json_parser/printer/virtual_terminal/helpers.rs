//! Helper functions for ANSI sequence processing.
//!
//! This module provides utility functions for stripping ANSI escape sequences
//! and applying carriage return overwrite semantics.

/// Strip ANSI escape sequences from a string.
///
/// This is a simplified implementation that removes common ANSI sequences
/// used in terminal output (SGR codes for colors/styles, cursor movement).
///
/// # Arguments
///
/// * `s` - The string to strip ANSI sequences from
///
/// # Returns
///
/// The string with ANSI sequences removed
pub fn strip_ansi_sequences(s: &str) -> String {
    // Simple regex-free implementation: skip \x1b[...m and \x1b[...A/B/K sequences
    let mut result = String::new();
    let mut chars = s.chars().peekable();

    while let Some(c) = chars.next() {
        if c == '\x1b' && chars.peek() == Some(&'[') {
            chars.next(); // consume '['
                          // Skip until we find a letter (command char)
            while let Some(&next_char) = chars.peek() {
                chars.next();
                if next_char.is_ascii_alphabetic() {
                    break;
                }
            }
        } else {
            result.push(c);
        }
    }
    result
}

pub fn apply_cr_overwrite_semantics(s: &str) -> String {
    // Simulate a log console that does NOT interpret ANSI escape codes, but DOES treat
    // carriage return as "return to start of current line" (common for progress output).
    //
    // Approach: process character-by-character, maintaining a current line buffer and
    // cursor position. `\n` commits the line, `\r` sets cursor to 0.
    let mut out = String::new();
    let mut line: Vec<char> = Vec::new();
    let mut col: usize = 0;

    for ch in s.chars() {
        match ch {
            '\n' => {
                out.extend(line.iter());
                out.push('\n');
                line.clear();
                col = 0;
            }
            '\r' => {
                col = 0;
            }
            _ => {
                if col >= line.len() {
                    line.resize(col + 1, ' ');
                }
                line[col] = ch;
                col = col.saturating_add(1);
            }
        }
    }

    out.extend(line.iter());
    out
}
