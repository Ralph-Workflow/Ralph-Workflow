//! Pure scan-policy helpers: match filtering, violation classification, and
//! glob-path matching.
//!
//! All functions here are pure (no filesystem access, no I/O side effects).
//! They are called by the I/O boundary layer in `crate::io::scanner` which
//! provides the raw bytes, line index, and check definitions.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::domain::string_search::{kmp_search, tw_contains_precomputed};
use crate::types::{LineIndex, MatchMode, NativeScanCheck, NativeScanViolation};

// ── Match-mode violation checks ───────────────────────────────────────────────

pub(crate) fn is_match_mode_violation(
    check: &NativeScanCheck,
    check_idx: usize,
    content: &[u8],
    line_idx: &LineIndex,
    byte_start: usize,
    byte_end: usize,
    tw_precomputed: &HashMap<usize, (usize, usize)>,
) -> bool {
    match check.mode {
        MatchMode::AnyLiteral { skip_comment_lines } => {
            !skip_comment_lines || !line_is_comment(content, line_idx, byte_start)
        }
        MatchMode::StemWithBoolSuffix => {
            word_boundary_at_start(content, byte_start) && matches_bool_suffix(content, byte_end)
        }
        MatchMode::AnyLiteralAtLineStart { skip_comment_lines } => {
            if skip_comment_lines && line_is_comment(content, line_idx, byte_start) {
                false
            } else {
                only_whitespace_before_on_line(content, line_idx, byte_start)
            }
        }
        MatchMode::NegativeLookahead {
            negative_context,
            word_boundary_at_end,
        } => is_negative_lookahead_violation(
            content,
            line_idx,
            byte_start,
            byte_end,
            negative_context,
            word_boundary_at_end,
            tw_precomputed.get(&check_idx).copied(),
        ),
    }
}

fn is_negative_lookahead_violation(
    content: &[u8],
    line_idx: &LineIndex,
    byte_start: usize,
    byte_end: usize,
    negative_context: &str,
    word_boundary_at_end: bool,
    precomputed: Option<(usize, usize)>,
) -> bool {
    if word_boundary_at_end && !is_word_boundary_at_end(content, byte_end) {
        return false;
    }
    if negative_context.is_empty() {
        return false;
    }
    let line_bytes = line_idx.extract_line(content, byte_start);
    match precomputed {
        Some(pc) => !tw_contains_precomputed(line_bytes, negative_context.as_bytes(), pc),
        None => true,
    }
}

// ── Forbidden allow/expect skip logic ─────────────────────────────────────────

pub(crate) fn should_skip_forbidden_allow_expect(
    check_name: &str,
    matched_literal: &str,
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
) -> bool {
    if check_name != "forbidden-allow-expect-scan" {
        return false;
    }

    if is_allowed_expect_with_reason(content, line_idx, byte_offset, matched_literal) {
        return true;
    }

    if is_cfg_attr_literal(matched_literal) {
        let line_bytes = line_idx.extract_line(content, byte_offset);
        if is_allowed_cfg_attr_expect_with_reason(content, line_idx, byte_offset, matched_literal) {
            return true;
        }
        if !line_contains_allow_or_expect(line_bytes) {
            return true;
        }
    }

    false
}

/// Return true when an `#[expect(...)]` match is permitted because it has a
/// non-empty `reason = "..."` and is an outer (item-scope) attribute.
///
/// Inner attributes (`#![expect(...)]`) are always violations regardless of reason,
/// because item scope (not module or crate) is required.
fn is_allowed_expect_with_reason(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
    matched_literal: &str,
) -> bool {
    // Only outer #[expect( is conditionally allowed; #![expect( is always blocked
    if matched_literal != "#[expect(" {
        return false;
    }
    // Check current and subsequent lines for reason (handles multi-line attributes).
    // try_fold: Err(result) to stop early, Ok(()) to continue scanning.
    let start = line_idx.line_number(byte_offset);
    (start..start + 10)
        .take_while(|&line| line < line_idx.newlines.len())
        .try_fold((), |(), line| {
            let line_bytes = line_idx.extract_line(content, line_idx.start_of_line(line));
            if line_has_nonempty_reason(line_bytes) {
                return Err(true);
            }
            if line > start && line_bytes.contains(&b')') {
                return Err(false);
            }
            Ok(())
        })
        .err()
        .unwrap_or(false)
}

/// Return true when a `#[cfg_attr(..., expect(...))]` match is permitted because
/// it wraps an expect with a non-empty `reason = "..."` and is an outer attribute.
fn is_allowed_cfg_attr_expect_with_reason(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
    matched_literal: &str,
) -> bool {
    // Only outer #[cfg_attr( is conditionally allowed
    if matched_literal != "#[cfg_attr(" {
        return false;
    }

    scan_cfg_attr_lines(content, line_idx, byte_offset)
}

fn scan_cfg_attr_lines(content: &[u8], line_idx: &LineIndex, byte_offset: usize) -> bool {
    let start = line_idx.line_number(byte_offset);
    // Accumulate (has_expect, has_reason) across up to 10 lines.
    // try_fold: Err(result) to stop early, Ok((has_expect, has_reason)) to continue.
    (start..start + 10)
        .take_while(|&line| line < line_idx.newlines.len())
        .try_fold((false, false), |(has_expect, has_reason), line| {
            let line_bytes = line_idx.extract_line(content, line_idx.start_of_line(line));
            let now_expect = has_expect || line_bytes.windows(7).any(|w| w == b"expect(");
            if line_bytes.windows(6).any(|w| w == b"allow(") && !now_expect {
                return Err(false);
            }
            let now_reason = has_reason || line_has_nonempty_reason(line_bytes);
            if now_expect && now_reason {
                return Err(true);
            }
            if line > start && line_bytes.contains(&b')') {
                return Err(now_expect && now_reason);
            }
            Ok((now_expect, now_reason))
        })
        .map_or_else(
            |stop_result| stop_result,
            |(has_expect, has_reason)| has_expect && has_reason,
        )
}

// ── Handle-match: dispatch + violation construction ───────────────────────────

/// Context bundle for a single Aho-Corasick match, passed to `handle_match`.
pub(crate) struct MatchContext<'a> {
    pub content: &'a [u8],
    pub line_idx: &'a LineIndex,
    pub check_idx: usize,
    pub check: &'a NativeScanCheck,
    pub matched_literal: &'a str,
    pub mat_start: usize,
    pub mat_end: usize,
    pub tw_precomputed: &'a HashMap<usize, (usize, usize)>,
}

/// Process a single Aho-Corasick match and return a `(check_idx, violation)` pair
/// if the match constitutes a real violation, or `None` if it should be filtered.
///
/// All policy decisions (mode filtering, allow/expect suppression) are pure and
/// operate only on the provided slices and maps.  The boundary layer in
/// `crate::io::scanner` is responsible for providing the raw bytes and calling
/// this function.
pub(crate) fn handle_match(
    file_path: &Path,
    ctx: &MatchContext<'_>,
    exclude_globs: &[&str],
) -> Option<(usize, NativeScanViolation)> {
    if file_is_excluded(file_path, exclude_globs) {
        return None;
    }
    if !is_match_mode_violation(
        ctx.check,
        ctx.check_idx,
        ctx.content,
        ctx.line_idx,
        ctx.mat_start,
        ctx.mat_end,
        ctx.tw_precomputed,
    ) {
        return None;
    }

    if should_skip_forbidden_allow_expect(
        ctx.check.name,
        ctx.matched_literal,
        ctx.content,
        ctx.line_idx,
        ctx.mat_start,
    ) {
        return None;
    }

    let line_number = ctx.line_idx.line_number(ctx.mat_start) + 1;
    let line =
        String::from_utf8_lossy(ctx.line_idx.extract_line(ctx.content, ctx.mat_start)).to_string();
    Some((
        ctx.check_idx,
        NativeScanViolation {
            file: file_path.to_path_buf(),
            line_number,
            line,
        },
    ))
}

// ── Glob-path classification ──────────────────────────────────────────────────

/// Classify how to process a filesystem entry during glob-based directory traversal.
pub(crate) enum GlobEntryAction {
    /// Skip this entry entirely.
    Skip,
    /// Recurse into this directory.
    Recurse(PathBuf),
    /// Include this file in results.
    Include(PathBuf),
}

/// Classify a filesystem path against include/exclude globs without performing
/// any I/O beyond what the caller already determined (i.e. `path.is_dir()`).
///
/// The `is_dir` and `matches_include` parameters avoid extra filesystem stat
/// calls; the boundary layer in `io/scanner.rs` evaluates those once from the
/// `DirEntry` metadata and passes the results here.
pub(crate) fn classify_glob_entry(
    path: &Path,
    is_dir: bool,
    exclude_globs: &[&str],
    matches_include: bool,
) -> GlobEntryAction {
    if file_is_excluded(path, exclude_globs) {
        GlobEntryAction::Skip
    } else if is_dir {
        GlobEntryAction::Recurse(path.to_path_buf())
    } else if matches_include {
        GlobEntryAction::Include(path.to_path_buf())
    } else {
        GlobEntryAction::Skip
    }
}

// ── Pure path-glob matching ────────────────────────────────────────────────────

/// Return true if `path` matches any of the `exclude_globs`.
pub(crate) fn file_is_excluded(path: &Path, exclude_globs: &[&str]) -> bool {
    exclude_globs
        .iter()
        .any(|g| file_matches_exclude_glob(path, g))
}

fn file_matches_exclude_glob(path: &Path, glob: &str) -> bool {
    // Exact file-name match (no path separators or wildcards): "_TEMPLATE.rs"
    if !glob.contains('/') && !glob.contains('*') {
        return path.file_name().and_then(|n| n.to_str()) == Some(glob);
    }
    // "**/<segment>/**" — any path component equals `segment`.
    if glob.starts_with("**/") && glob.ends_with("/**") {
        let segment = &glob[3..glob.len() - 3];
        return path
            .components()
            .any(|c| c.as_os_str().to_str() == Some(segment));
    }
    // Fallback: exact file-name match.
    path.file_name().and_then(|n| n.to_str()) == Some(glob)
}

// ── Line-content helpers ──────────────────────────────────────────────────────

/// Return true when the line containing `byte_offset` starts with `//`
/// (ignoring leading whitespace).
pub(crate) fn line_is_comment(content: &[u8], line_idx: &LineIndex, byte_offset: usize) -> bool {
    let line_start = line_idx.line_start(byte_offset);
    let trimmed = &content[line_start..];
    let non_ws = trimmed.iter().position(|&b| b != b' ' && b != b'\t');
    match non_ws {
        Some(i) => trimmed[i..].starts_with(b"//"),
        None => false,
    }
}

/// Return true when the character immediately before `start` is NOT an
/// ASCII alphanumeric character or underscore (word-boundary at start).
pub(crate) fn word_boundary_at_start(content: &[u8], start: usize) -> bool {
    if start == 0 {
        return true;
    }
    let prev = content[start - 1];
    !prev.is_ascii_alphanumeric() && prev != b'_'
}

/// Return true when only whitespace (spaces or tabs) precedes `byte_offset`
/// on the same line.  An empty prefix (match at column 0) also returns true.
pub(crate) fn only_whitespace_before_on_line(
    content: &[u8],
    line_idx: &LineIndex,
    byte_offset: usize,
) -> bool {
    let line_start = line_idx.line_start(byte_offset);
    content[line_start..byte_offset]
        .iter()
        .all(|&b| b == b' ' || b == b'\t')
}

/// Return true if the literal is a cfg_attr variant.
pub(crate) fn is_cfg_attr_literal(lit: &str) -> bool {
    lit == "#[cfg_attr(" || lit == "#![cfg_attr("
}

/// Return true if the line contains allow( or expect(.
pub(crate) fn line_contains_allow_or_expect(line: &[u8]) -> bool {
    line.windows(6).any(|w| w == b"allow(") || line.windows(7).any(|w| w == b"expect(")
}

/// Return true when the character immediately after `end` is NOT an ASCII
/// alphanumeric character or underscore (word boundary at end of literal).
pub(crate) fn is_word_boundary_at_end(content: &[u8], end: usize) -> bool {
    if end >= content.len() {
        return true;
    }
    let next = content[end];
    !next.is_ascii_alphanumeric() && next != b'_'
}

// ── Bool-suffix and reason helpers ────────────────────────────────────────────

/// Return true when the bytes at `end` match `\s*:\s*bool` followed by a
/// non-identifier character (or end of input).
fn matches_bool_suffix(content: &[u8], end: usize) -> bool {
    let after_colon = skip_whitespace(&content[end..]);
    if after_colon.is_empty() || after_colon[0] != b':' {
        return false;
    }
    // Use KMP (TAOCP Vol. 3, §6.4) for guaranteed O(n+m) suffix search.
    let after_ws = skip_whitespace(&after_colon[1..]);
    if kmp_search(after_ws, b"bool") != Some(0) {
        return false;
    }
    // Must be followed by a non-identifier character or end of input.
    after_ws
        .get(4)
        .is_none_or(|&next| !next.is_ascii_alphanumeric() && next != b'_')
}

/// Return true when the line contains `reason = "..."` with a non-empty reason string.
pub(crate) fn line_has_nonempty_reason(line: &[u8]) -> bool {
    let Some(pos) = kmp_search(line, b"reason") else {
        return false;
    };
    parse_reason_value(&line[pos + 6..])
}

fn parse_reason_value(rest: &[u8]) -> bool {
    let rest = skip_whitespace(rest);
    if rest.is_empty() || rest[0] != b'=' {
        return false;
    }
    let rest = skip_whitespace(&rest[1..]);
    if rest.is_empty() || rest[0] != b'"' {
        return false;
    }
    // next char must not be closing '"' (non-empty reason)
    rest.len() > 1 && rest[1] != b'"'
}

fn skip_whitespace(s: &[u8]) -> &[u8] {
    let i = s
        .iter()
        .position(|&b| b != b' ' && b != b'\t' && b != b'\n' && b != b'\r')
        .unwrap_or(s.len());
    &s[i..]
}
