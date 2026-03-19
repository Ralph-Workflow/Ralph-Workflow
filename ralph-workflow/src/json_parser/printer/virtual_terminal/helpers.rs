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
    let mut result = String::new();
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

pub fn apply_cr_overwrite_semantics(s: &str) -> String {
    // Simulate a log console that does NOT interpret ANSI escape codes, but DOES treat
    // carriage return as "return to start of current line" (common for progress output).
    //
    // Approach: process character-by-character, maintaining a current line buffer and
    // cursor position. `\n` commits the line, `\r` sets cursor to 0.
    let mut out = String::new();
    let mut line: Vec<char> = Vec::new();
    let mut col: usize = 0;

    s.chars().for_each(|ch| match ch {
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
    });

    out.extend(line.iter());
    out
}
