use aho_corasick::{AhoCorasick, AhoCorasickBuilder, AhoCorasickKind};
use std::sync::OnceLock;

use crate::io::native_scan_types::LineIndex;

/// Result of scanning command output for Cargo/compiler diagnostic prefixes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticLevel {
    /// No error or warning diagnostic found.
    Clean,
    /// At least one "warning:" prefix found (and no error).
    Warning,
    /// At least one "error:" or "Error:" prefix found.
    Error,
}

impl DiagnosticLevel {
    /// Returns the more severe of two diagnostic levels.
    /// Error > Warning > Clean.
    pub(crate) fn max_level(self, other: DiagnosticLevel) -> DiagnosticLevel {
        match (self, other) {
            (DiagnosticLevel::Error, _) | (_, DiagnosticLevel::Error) => DiagnosticLevel::Error,
            (DiagnosticLevel::Warning, _) | (_, DiagnosticLevel::Warning) => {
                DiagnosticLevel::Warning
            }
            _ => DiagnosticLevel::Clean,
        }
    }
}

// Pattern IDs for the diagnostic Aho-Corasick automaton.
const DIAG_PAT_ERROR_LC: usize = 0; // "error:"
const DIAG_PAT_ERROR_TC: usize = 1; // "Error:"
const DIAG_PAT_ERROR_BRACKET: usize = 2; // "error[" (rustc / clippy: error[E0XXX])
const DIAG_PAT_WARNING_LC: usize = 3; // "warning:"
const DIAG_PAT_WARNING_TC: usize = 4; // "Warning:" (defensive; kept for completeness)
const DIAG_PAT_WARNING_BRACKET: usize = 5; // "warning[" (rustc warnings can use this form)

static DIAG_PATTERNS: &[&str] = &[
    "error:", "Error:", "error[", "warning:", "Warning:", "warning[",
];

/// Process-global cached Aho-Corasick automaton for diagnostic-prefix detection.
///
/// Built exactly once via `OnceLock` (Knuth, TAOCP Vol. 2 §4.3: avoid redundant
/// computation by memoising results that are guaranteed identical on every call).
/// The automaton is constructed from the static `DIAG_PATTERNS` slice which is
/// never mutated, so the cached value is correct for the entire process lifetime.
static DIAG_AC: OnceLock<AhoCorasick> = OnceLock::new();

fn diag_ac() -> &'static AhoCorasick {
    // Explicit DFA mode: 6 short ASCII patterns → tiny state count, O(1) per-character
    // transitions (vs. NFA's amortised O(k) cost). The DFA fits comfortably in L1 cache.
    // Reference: Hopcroft (1971) DFA minimisation; TAOCP Vol. 3 §6.3.
    // Fallback to Auto (NFA/hybrid) if the crate rejects the DFA build (never expected
    // for 6 small ASCII patterns, but correctness must not be sacrificed for speed).
    DIAG_AC.get_or_init(|| {
        AhoCorasickBuilder::new()
            .kind(Some(AhoCorasickKind::DFA))
            .build(DIAG_PATTERNS)
            .unwrap_or_else(|_| {
                AhoCorasick::new(DIAG_PATTERNS).expect("static diagnostic patterns are valid")
            })
    })
}

/// Scan command output (stdout or stderr) for diagnostic-level prefixes in O(n+m).
///
/// Uses a single Aho-Corasick pass (Aho & Corasick, 1975) over the raw bytes.
/// For each match, verifies the pattern appears at the start of a trimmed line
/// (equivalent to `line.trim_start().starts_with(pattern)`) via a LineIndex O(log L)
/// lookup (TAOCP Vol. 3 §6.2.1 Algorithm B).
///
/// The Aho-Corasick automaton is constructed once per process via `OnceLock`
/// and reused on every call, eliminating redundant O(m) construction overhead.
///
/// Returns `DiagnosticLevel::Error` if any error pattern is found at line start,
/// `DiagnosticLevel::Warning` if any warning pattern (but no error) is found,
/// `DiagnosticLevel::Clean` otherwise.
pub fn scan_has_diagnostic_prefix(text: &str) -> DiagnosticLevel {
    if text.is_empty() {
        return DiagnosticLevel::Clean;
    }
    let bytes = text.as_bytes();
    let line_idx = LineIndex::new(bytes);
    diag_ac()
        .find_iter(bytes)
        .filter(|mat| is_match_at_line_start(bytes, &line_idx, mat.start()))
        .filter_map(|mat| pattern_level(mat.pattern().as_usize()))
        .fold(DiagnosticLevel::Clean, DiagnosticLevel::max_level)
}

fn is_match_at_line_start(bytes: &[u8], line_idx: &LineIndex, byte_start: usize) -> bool {
    bytes[line_idx.line_start(byte_start)..byte_start]
        .iter()
        .all(|&b| b.is_ascii_whitespace())
}

fn pattern_level(pattern_id: usize) -> Option<DiagnosticLevel> {
    match pattern_id {
        DIAG_PAT_ERROR_LC | DIAG_PAT_ERROR_TC | DIAG_PAT_ERROR_BRACKET => {
            Some(DiagnosticLevel::Error)
        }
        DIAG_PAT_WARNING_LC | DIAG_PAT_WARNING_TC | DIAG_PAT_WARNING_BRACKET => {
            Some(DiagnosticLevel::Warning)
        }
        _ => None,
    }
}
