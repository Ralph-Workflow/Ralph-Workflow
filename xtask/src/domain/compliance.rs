//! Pure domain helpers for the compliance checks.
//!
//! These helpers decide what message/status to emit after the boundary layer
//! has already gathered the raw data (file lists, violation traces, errors).

const STATUS_PASS: u8 = 0;
const STATUS_WARNING: u8 = 1;
const STATUS_ERROR: u8 = 2;

/// Summary produced by the domain layer for a compliance check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComplianceSummary {
    pub status_code: u8,
    pub message: String,
}

impl ComplianceSummary {
    fn new(status_code: u8, message: String) -> Self {
        Self {
            status_code,
            message,
        }
    }
}

pub fn shell_script_scan_result(found: &[String], walk_errors: &[String]) -> ComplianceSummary {
    if !walk_errors.is_empty() {
        return ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Failed to scan for .sh files due to directory walk errors:\n{}",
                walk_errors.join("\n")
            ),
        );
    }

    if found.is_empty() {
        ComplianceSummary::new(STATUS_PASS, String::new())
    } else {
        ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Found {} .sh file(s) that must not exist after the shell-script migration:\n{}",
                found.len(),
                found.join("\n")
            ),
        )
    }
}

pub fn timeout_wrapper_scan_result(
    violations: &[String],
    read_errors: &[String],
) -> ComplianceSummary {
    if !read_errors.is_empty() {
        return ComplianceSummary::new(
            STATUS_ERROR,
            format!(
                "Failed to read {} integration test file(s) during timeout-wrapper compliance scan:\n{}",
                read_errors.len(),
                read_errors.join("\n")
            ),
        );
    }

    if violations.is_empty() {
        ComplianceSummary::new(STATUS_PASS, String::new())
    } else {
        ComplianceSummary::new(
            STATUS_WARNING,
            format!(
                "Found {} test(s) missing timeout wrapper:\n{}",
                violations.len(),
                violations.join("\n")
            ),
        )
    }
}

/// Trim leading and trailing ASCII whitespace (space, tab, carriage-return)
/// from a byte slice.
pub(crate) fn trim_ascii(b: &[u8]) -> &[u8] {
    let is_ws = |&x: &u8| x == b' ' || x == b'\t' || x == b'\r';
    let start = b.iter().position(|x| !is_ws(x)).unwrap_or(b.len());
    // Find the last non-whitespace byte by scanning from the right via position on reversed iter.
    let end = b
        .iter()
        .rev()
        .position(|x| !is_ws(x))
        .map(|rev_pos| b.len() - rev_pos)
        .unwrap_or(0);
    if start >= end {
        &[]
    } else {
        &b[start..end]
    }
}

pub(crate) fn is_fn_decl(line: &str) -> bool {
    let trimmed = line.trim();
    // Match: fn, pub fn, async fn, pub async fn, unsafe fn, pub unsafe fn, etc.
    let after_visibility = trimmed.strip_prefix("pub ").unwrap_or(trimmed);
    let after_async = after_visibility
        .strip_prefix("async ")
        .unwrap_or(after_visibility);
    let after_unsafe = after_async.strip_prefix("unsafe ").unwrap_or(after_async);
    after_unsafe.starts_with("fn ")
}

pub(crate) fn extract_test_name(line: &str) -> Option<&str> {
    let after_fn = line.split_once("fn ")?.1;
    let name_end = after_fn
        .find(|c: char| !c.is_alphanumeric() && c != '_')
        .unwrap_or(after_fn.len());
    if name_end == 0 {
        return None;
    }
    Some(&after_fn[..name_end])
}

/// Find the index of the first line (in `lines`) at or after `from_idx`
/// that contains `{`, within `lookahead` additional lines.
pub(crate) fn find_opening_brace_in_lines(
    lines: &[&[u8]],
    from_idx: usize,
    lookahead: usize,
) -> Option<usize> {
    let end = std::cmp::min(from_idx + lookahead + 1, lines.len());
    lines[from_idx..end]
        .iter()
        .enumerate()
        .find_map(|(offset, line)| {
            if line.contains(&b'{') {
                Some(from_idx + offset)
            } else {
                None
            }
        })
}

pub(crate) fn find_fn_line_idx(lines: &[&[u8]], start_idx: usize) -> Option<usize> {
    const MAX_FN_LOOKAHEAD_LINES: usize = 8;
    let n = lines.len();
    // try_fold: Ok(()) means "keep scanning", Err(Some(idx)) means "found fn",
    // Err(None) means "hit a non-fn non-skip line, stop".
    let result = (start_idx..start_idx + MAX_FN_LOOKAHEAD_LINES)
        .take_while(|&idx| idx < n)
        .try_fold((), |(), idx| match classify_fn_line_action(lines, idx) {
            FnLineAction::Skip => Ok(()),
            FnLineAction::Found => Err(Some(idx)),
            FnLineAction::Stop => Err(None),
        });
    match result {
        Err(found) => found,
        Ok(()) => None,
    }
}

enum FnLineAction {
    Skip,
    Found,
    Stop,
}

fn classify_fn_line_action(lines: &[&[u8]], idx: usize) -> FnLineAction {
    let trimmed = trim_ascii(lines[idx]);
    if trimmed.is_empty() || trimmed.starts_with(b"#") || trimmed.starts_with(b"//") {
        return FnLineAction::Skip;
    }
    match std::str::from_utf8(lines[idx]) {
        Ok(s) if is_fn_decl(s) => FnLineAction::Found,
        _ => FnLineAction::Stop,
    }
}

/// Find the end of a function body by tracking brace depth in raw bytes.
///
/// Scans `content[start..scan_end]` counting `{` and `}` bytes.  Returns the
/// byte offset **one past** the closing `}` when depth reaches 0, or
/// `scan_end` if the body is not closed within the scan window.
pub(crate) fn find_function_end_bytes(content: &[u8], start: usize, scan_end: usize) -> usize {
    let scan_end = scan_end.min(content.len());
    // try_fold returns Err(end_byte) when the closing brace is found (depth==0 after a '}'),
    // or Ok(final_depth) if the scan window is exhausted without closing the body.
    let result = content[start..scan_end]
        .iter()
        .enumerate()
        .try_fold(0i32, |depth, (i, &b)| {
            let new_depth = match b {
                b'{' => depth + 1,
                b'}' => depth - 1,
                _ => depth,
            };
            if new_depth == 0 && b == b'}' {
                Err(start + i + 1)
            } else {
                Ok(new_depth)
            }
        });
    match result {
        Err(end_pos) => end_pos,
        Ok(_) => scan_end,
    }
}
