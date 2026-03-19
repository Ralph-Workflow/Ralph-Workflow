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

pub fn apply_cr_overwrite_semantics(s: &str) -> String {
    let (out, _, _): (String, Vec<char>, usize) = s.chars().fold(
        (String::new(), Vec::new(), 0usize),
        |(mut out, mut line, col): (String, Vec<char>, usize), ch| match ch {
            '\n' => {
                out.extend(line.iter());
                out.push('\n');
                (out, Vec::new(), 0)
            }
            '\r' => (out, line, 0),
            _ => {
                let new_col = col.saturating_add(1);
                let new_line: Vec<char> = line
                    .iter()
                    .enumerate()
                    .map(|(i, &c)| if i == col { ch } else { c })
                    .chain(std::iter::repeat(' ').take((col + 1).saturating_sub(line.len())))
                    .take(col + 1)
                    .collect();
                (out, new_line, new_col)
            }
        },
    );
    out
}
