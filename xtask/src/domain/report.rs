//! Pure report-generation logic with no I/O or side effects.
//!
//! This module contains pure text-parsing functions for extracting structured
//! information from compiler/linter output.

/// Collect contiguous error/warning blocks from compiler output.
///
/// Each block starts at a line that begins with `error:` or `warning:` and
/// ends at the first blank line after the block starts.
pub(crate) fn collect_error_blocks(output: &str) -> Vec<String> {
    let (blocks, last) = output.lines().fold(
        (Vec::<String>::new(), Option::<String>::None),
        |(blocks, current), line| fold_error_line(blocks, current, line),
    );
    finalize_blocks(blocks, last)
}

fn fold_error_line(
    blocks: Vec<String>,
    current: Option<String>,
    line: &str,
) -> (Vec<String>, Option<String>) {
    let (blocks, current) = if is_error_start(line) {
        flush_block(blocks, current)
    } else {
        (blocks, current)
    };
    let current = current.map(|buf| append_line_to_block(buf, line));
    let current =
        current.or_else(|| is_error_start(line).then(|| append_line_to_block(String::new(), line)));
    if line.trim().is_empty() {
        flush_block(blocks, current)
    } else {
        (blocks, current)
    }
}

fn append_line_to_block(buf: String, line: &str) -> String {
    format!("{buf}{line}\n")
}

fn flush_block(blocks: Vec<String>, current: Option<String>) -> (Vec<String>, Option<String>) {
    let new_blocks = match current {
        Some(block) if !block.trim().is_empty() => blocks.into_iter().chain([block]).collect(),
        _ => blocks,
    };
    (new_blocks, None)
}

fn finalize_blocks(blocks: Vec<String>, last: Option<String>) -> Vec<String> {
    match last {
        Some(block) if !block.trim().is_empty() => blocks.into_iter().chain([block]).collect(),
        _ => blocks,
    }
}

fn is_error_start(line: &str) -> bool {
    let trimmed = line.trim_start();
    trimmed.starts_with("error:") || trimmed.starts_with("warning:")
}
