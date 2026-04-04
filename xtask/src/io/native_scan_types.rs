//! Native scan types for multi-pattern file scanning.
//!
//! These types are shared across the native scan implementation and are public
//! primarily so `compliance.rs` can use `LineIndex` for its Aho-Corasick scan.

use std::path::PathBuf;

/// Matching strategy for a native scan check.
///
/// This is a `const`-constructible enum so `NativeScanCheck` instances can
/// live in `static` arrays.
#[derive(Clone, Copy)]
pub enum MatchMode {
    /// Fail if any of the check's `literals` appear in a scanned file.
    /// When `skip_comment_lines` is true, matches on lines whose non-whitespace
    /// prefix is `//` are ignored (avoids false positives in doc-comments).
    AnyLiteral { skip_comment_lines: bool },
    /// Fail if any of the check's `literals` (stem identifiers) appear in a
    /// scanned file AND the text immediately following the match is
    /// `<optional-whitespace>:<optional-whitespace>bool`.
    ///
    /// Also enforces a word-boundary check at the match start so that
    /// `is_testing_mode` does not trigger on the `is_test` stem.
    StemWithBoolSuffix,
    /// Fail if the literal appears AND only whitespace (or nothing) precedes it
    /// on the same line.  Equivalent to the rg anchor `^\s*literal`.
    ///
    /// When `skip_comment_lines` is true, matches on lines whose non-whitespace
    /// prefix is `//` are ignored (avoids false positives in doc-comments).
    ///
    /// This provides O(n+m+z) Aho-Corasick detection with an O(k) per-match
    /// post-filter for the line-start check (k = length of prefix slice).
    AnyLiteralAtLineStart { skip_comment_lines: bool },
    /// Fail if the literal appears at a word boundary (next char is not
    /// alphanumeric/underscore, when `word_boundary_at_end` is true) AND the
    /// `negative_context` string is absent from the same line.
    ///
    /// Implements a native negative-lookahead: the violation is triggered only
    /// when the literal is found BUT the negative_context is NOT present on the
    /// same line.  This replaces a PCRE2 `(?!.*ctx)` pattern.
    NegativeLookahead {
        /// If present on the same line, the match is NOT a violation.
        negative_context: &'static str,
        /// When true, the character immediately after the literal must not be
        /// an ASCII alphanumeric character or underscore (word boundary).
        word_boundary_at_end: bool,
    },
}

/// A single native scan check definition.
///
/// `NativeScanCheck` values are `const`-constructible for use in static arrays.
pub struct NativeScanCheck {
    pub name: &'static str,
    /// Literal byte-strings to search for with Aho-Corasick.
    pub literals: &'static [&'static str],
    /// Directories to scan, relative to the repo root.
    pub directories: &'static [&'static str],
    /// Glob pattern selecting which files to include (e.g. `"*.rs"`).
    pub include_glob: &'static str,
    /// File-name or path globs to exclude from scanning.
    /// Supports `"<name>.rs"` (exact file-name) and `"**/<seg>/**"` (path component).
    pub exclude_globs: &'static [&'static str],
    pub mode: MatchMode,
}

/// A single line-level violation reported by a native scan check.
#[derive(Debug)]
pub struct NativeScanViolation {
    pub file: PathBuf,
    /// 1-based line number.
    pub line_number: usize,
    /// The full text of the offending line (without trailing newline).
    pub line: String,
}

/// The result of running one `NativeScanCheck`.
pub struct NativeScanCheckResult {
    pub check_name: &'static str,
    /// `true` iff no violations were found.
    pub passed: bool,
    /// Populated when `passed == false`.
    pub violations: Vec<NativeScanViolation>,
}

/// Pre-built line index enabling O(log L) line-context lookups via binary search.
///
/// Construction is a single O(n) pass collecting newline byte-offsets into a
/// sorted Vec.  All subsequent lookups use Knuth's binary search algorithm
/// (TAOCP Vol. 3, §6.2.1 Algorithm B) via `partition_point`, giving O(log L)
/// per query instead of the O(n) linear scan in the original helpers.
///
/// `pub` visibility is intentional: `compliance.rs` shares this index for its
/// Aho-Corasick timeout-wrapper scan, avoiding a duplicate implementation.
pub struct LineIndex {
    /// Byte offsets of every b'\n' in the source, in ascending order.
    pub newlines: Vec<usize>,
    /// Total byte length of the source buffer.
    pub content_len: usize,
}

impl LineIndex {
    /// Build the index from raw file bytes in O(n).
    pub fn new(content: &[u8]) -> Self {
        let newlines: Vec<usize> = content
            .iter()
            .enumerate()
            .filter_map(|(i, &b)| if b == b'\n' { Some(i) } else { None })
            .collect();
        Self {
            newlines,
            content_len: content.len(),
        }
    }

    /// 0-based line number of the line that contains `offset`.
    /// Uses binary search (TAOCP Vol. 3, §6.2.1 Algorithm B): O(log L).
    ///
    /// The number of newlines strictly before `offset` equals the 0-based
    /// line number of the line containing `offset`.
    pub fn line_number(&self, offset: usize) -> usize {
        self.newlines.partition_point(|&nl| nl < offset)
    }

    /// Byte offset of the first byte of the line containing `offset`.
    pub fn line_start(&self, offset: usize) -> usize {
        let idx = self.newlines.partition_point(|&nl| nl < offset);
        if idx == 0 {
            0
        } else {
            self.newlines[idx - 1] + 1
        }
    }

    /// Byte offset of the `\n` terminating the line containing `offset`,
    /// or `content_len` if the line has no trailing newline.
    ///
    /// When `offset` is exactly at a `\n`, that newline is the line terminator,
    /// so its offset is returned (the line ends at the newline itself).
    pub fn line_end(&self, offset: usize) -> usize {
        // Use strict `<` so that when `offset` IS the newline byte,
        // we return that newline offset (not the next one).
        let idx = self.newlines.partition_point(|&nl| nl < offset);
        if idx < self.newlines.len() {
            self.newlines[idx]
        } else {
            self.content_len
        }
    }

    /// Extract the raw bytes of the line containing `offset` (without the
    /// trailing `\n`).
    pub fn extract_line<'a>(&self, content: &'a [u8], offset: usize) -> &'a [u8] {
        let start = self.line_start(offset);
        let end = self.line_end(offset);
        &content[start..end]
    }

    /// Byte offset of the first byte of line number `line_num` (0-based).
    ///
    /// O(1) direct index into the newlines vec, avoiding a binary search.
    /// Returns `content_len` when `line_num` is past the end of the file.
    pub fn start_of_line(&self, line_num: usize) -> usize {
        if line_num == 0 {
            0
        } else if line_num <= self.newlines.len() {
            self.newlines[line_num - 1] + 1
        } else {
            self.content_len
        }
    }
}
