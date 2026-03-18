// Display utilities for delta rendering.
//
// Contains constants and helper functions for sanitizing content for display.

/// ANSI escape sequence for clearing the entire line.
///
/// This is more complete than `\x1b[0K` which only clears to the end of line.
/// Using `\x1b[2K` ensures the entire line is cleared during in-place updates.
pub const CLEAR_LINE: &str = "\x1b[2K";

/// Sanitize content for single-line display during streaming.
///
/// This function prepares streamed content for in-place terminal display by:
/// - Replacing newlines with spaces (to prevent artificial line breaks)
/// - Collapsing multiple consecutive whitespace characters into single spaces
/// - Trimming leading and trailing whitespace
///
/// NOTE: This function does NOT truncate to terminal width. Truncation during
/// streaming causes visible "..." cut-offs as content accumulates. Terminal width
/// truncation should only be applied for final/non-streaming display.
///
/// # Arguments
/// * `content` - The raw content to sanitize
///
/// # Returns
/// A sanitized string suitable for single-line display, without truncation.
#[must_use]
pub fn sanitize_for_display(content: &str) -> String {
    let result: String = content
        .chars()
        .fold((String::new(), false), |(mut result, prev_ws), ch| {
            if ch.is_whitespace() {
                if !prev_ws {
                    result.push(' ');
                }
                (result, true)
            } else {
                result.push(ch);
                (result, false)
            }
        })
        .0;

    result.trim().to_string()
}
