// Threshold configuration and overlap detection for deduplication.
//
// Contains:
// - ThresholdEnvironment trait for testability
// - Configuration constants
// - OverlapThresholds struct
// - Boundary detection (is_safe_boundary)
// - OverlapScore struct and scoring functions

/// Trait for accessing environment variables.
///
/// This trait enables dependency injection for testing without global state pollution.
pub trait ThresholdEnvironment {
    /// Get an environment variable by name.
    fn get_var(&self, name: &str) -> Option<String>;
}

/// Production implementation that reads from actual environment.
pub struct RealThresholdEnvironment;

impl ThresholdEnvironment for RealThresholdEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }
}

// ============================================================================
// Configuration Constants for Strong Overlap Detection
// ============================================================================

/// Default minimum overlap character count for deduplication.
///
/// Overlaps must be at least this many characters to be considered for deduplication.
/// This prevents false positives from short accidental matches (e.g., "the", "and").
const DEFAULT_MIN_OVERLAP_CHARS: usize = 30;

/// Minimum overlap ratio expressed as integer (50 = 50%).
/// Used for integer-based ratio comparison to avoid floating point issues.
const MIN_OVERLAP_RATIO_INT: usize = 50;

/// Default threshold for considering a chunk "short".
///
/// Short chunks (< this many chars) are never deduped unless they're exact matches
/// with the accumulated content. This prevents aggressive deduplication of tokens
/// like ".", "\n", "Ok" that are legitimately repeated.
const DEFAULT_SHORT_CHUNK_THRESHOLD: usize = 20;

/// Default threshold for consecutive duplicate detection.
///
/// If the exact same chunk arrives this many times in a row, it's treated as a
/// resend glitch and dropped entirely.
const DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD: usize = 3;

/// Minimum allowed value for `MIN_OVERLAP_CHARS`.
const MIN_MIN_OVERLAP_CHARS: usize = 10;

/// Maximum allowed value for `MIN_OVERLAP_CHARS`.
const MAX_MIN_OVERLAP_CHARS: usize = 100;

/// Minimum allowed value for `SHORT_CHUNK_THRESHOLD`.
const MIN_SHORT_CHUNK_THRESHOLD: usize = 5;

/// Maximum allowed value for `SHORT_CHUNK_THRESHOLD`.
const MAX_SHORT_CHUNK_THRESHOLD: usize = 50;

/// Minimum allowed value for `CONSECUTIVE_DUPLICATE_THRESHOLD`.
const MIN_CONSECUTIVE_DUPLICATE_THRESHOLD: usize = 2;

/// Maximum allowed value for `CONSECUTIVE_DUPLICATE_THRESHOLD`.
const MAX_CONSECUTIVE_DUPLICATE_THRESHOLD: usize = 10;

/// Configuration for strong overlap detection.
///
/// This struct holds the tunable thresholds that determine when an overlap
/// is "strong enough" to warrant deduplication.
#[derive(Debug, Clone, Copy)]
pub struct OverlapThresholds {
    /// Minimum character count for overlap
    pub min_overlap_chars: usize,
    /// Threshold below which chunks are considered "short"
    pub short_chunk_threshold: usize,
    /// Number of consecutive duplicates before aggressive dedupe
    pub consecutive_duplicate_threshold: usize,
}

impl Default for OverlapThresholds {
    fn default() -> Self {
        Self {
            min_overlap_chars: DEFAULT_MIN_OVERLAP_CHARS,
            short_chunk_threshold: DEFAULT_SHORT_CHUNK_THRESHOLD,
            consecutive_duplicate_threshold: DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD,
        }
    }
}

/// Testable variant that accepts an environment trait for dependency injection.
///
/// This allows tests to mock environment variables without global state pollution.
///
/// Reads the following environment variables:
/// - `RALPH_STREAMING_MIN_OVERLAP_CHARS`: Minimum overlap characters (default: 30, range: 10-100)
/// - `RALPH_STREAMING_SHORT_CHUNK_THRESHOLD`: Short chunk threshold (default: 20, range: 5-50)
/// - `RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD`: Consecutive duplicate threshold (default: 3, range: 2-10)
pub fn get_overlap_thresholds_with_env(env: &dyn ThresholdEnvironment) -> OverlapThresholds {
    let min_overlap_chars = env
        .get_var("RALPH_STREAMING_MIN_OVERLAP_CHARS")
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|v| {
            if (MIN_MIN_OVERLAP_CHARS..=MAX_MIN_OVERLAP_CHARS).contains(&v) {
                Some(v)
            } else {
                None
            }
        })
        .unwrap_or(DEFAULT_MIN_OVERLAP_CHARS);

    let short_chunk_threshold = env
        .get_var("RALPH_STREAMING_SHORT_CHUNK_THRESHOLD")
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|v| {
            if (MIN_SHORT_CHUNK_THRESHOLD..=MAX_SHORT_CHUNK_THRESHOLD).contains(&v) {
                Some(v)
            } else {
                None
            }
        })
        .unwrap_or(DEFAULT_SHORT_CHUNK_THRESHOLD);

    let consecutive_duplicate_threshold = env
        .get_var("RALPH_STREAMING_CONSECUTIVE_DUPLICATE_THRESHOLD")
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|v| {
            if (MIN_CONSECUTIVE_DUPLICATE_THRESHOLD..=MAX_CONSECUTIVE_DUPLICATE_THRESHOLD)
                .contains(&v)
            {
                Some(v)
            } else {
                None
            }
        })
        .unwrap_or(DEFAULT_CONSECUTIVE_DUPLICATE_THRESHOLD);

    OverlapThresholds {
        min_overlap_chars,
        short_chunk_threshold,
        consecutive_duplicate_threshold,
    }
}

pub fn get_overlap_thresholds() -> OverlapThresholds {
    get_overlap_thresholds_with_env(&RealThresholdEnvironment)
}

// ============================================================================
// Boundary Detection
// ============================================================================

/// Check if a character position is at a safe boundary for deduplication.
///
/// A "safe boundary" is where the overlap ends at a natural break point in text:
/// - Whitespace (space, tab, newline, etc.)
/// - ASCII punctuation (.,!?;:, etc.)
/// - End of string
///
/// This prevents deduplication from splitting words or tokens mid-way through,
/// which could cause incorrect rendering of intentional repetitions.
///
/// # Arguments
/// * `text` - The text to check
/// * `pos` - The position in the text (byte offset)
///
/// # Returns
/// * `true` - The position is at a safe boundary for deduplication
/// * `false` - The position is NOT at a safe boundary (mid-word, etc.)
///
/// # Examples
///
/// ```ignore
/// // Safe: overlap ends at space
/// assert!(is_safe_boundary("Hello World", 11)); // After "World"
///
/// // Safe: overlap ends at punctuation
/// assert!(is_safe_boundary("Hello, World!", 12)); // After "!"
///
/// // Unsafe: overlap ends mid-word
/// assert!(!is_safe_boundary("HelloWorld", 5)); // After "Hello"
/// ```
fn is_safe_boundary(text: &str, pos: usize) -> bool {
    // End of string is always safe
    if pos >= text.len() {
        return true;
    }

    // Get the character at the boundary position
    // We need to use character iteration for Unicode safety
    let char_at_pos = text[pos..].chars().next();

    char_at_pos
        .is_none_or(|c| c.is_whitespace() || c.is_ascii_punctuation() || c.is_ascii_control())
}

// ============================================================================
// Overlap Quality Scoring
// ============================================================================

/// Score representing the "strength" of an overlap.
///
/// This struct captures multiple metrics about an overlap to determine
/// if it's strong enough to warrant deduplication.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OverlapScore {
    /// Character count of the overlap
    pub char_count: usize,
    /// Whether the overlap meets the minimum ratio threshold
    pub ratio_met: bool,
    /// Whether the overlap ends at a safe boundary
    pub is_safe_boundary: bool,
}

impl OverlapScore {
    /// Check if this overlap meets all thresholds for deduplication.
    ///
    /// # Arguments
    /// * `thresholds` - The overlap thresholds to check against
    ///
    /// # Returns
    /// * `true` - The overlap is strong enough for deduplication
    /// * `false` - The overlap is too weak
    #[must_use]
    pub const fn meets_thresholds(&self, thresholds: &OverlapThresholds) -> bool {
        self.char_count >= thresholds.min_overlap_chars && self.ratio_met && self.is_safe_boundary
    }

    /// Check if the delta is short (below short chunk threshold).
    ///
    /// # Arguments
    /// * `delta_len` - The length of the delta
    /// * `thresholds` - The overlap thresholds
    ///
    /// # Returns
    /// * `true` - The delta is considered short
    /// * `false` - The delta is normal length
    #[must_use]
    #[cfg(test)]
    pub const fn is_short_delta(delta_len: usize, thresholds: &OverlapThresholds) -> bool {
        delta_len < thresholds.short_chunk_threshold
    }
}

/// Score the quality of an overlap between delta and accumulated content.
///
/// This function computes multiple metrics about an overlap to determine
/// if it's strong enough to warrant deduplication.
///
/// # Arguments
/// * `delta` - The incoming delta
/// * `accumulated` - The previously accumulated content
///
/// # Returns
/// An `OverlapScore` containing:
/// - `char_count`: The length of the overlap in characters
/// - `ratio`: The overlap as a fraction of delta length
/// - `is_safe_boundary`: Whether the overlap ends at a safe boundary
///
/// # Examples
///
/// ```ignore
/// // Strong overlap (30+ chars, 50%+ ratio, safe boundary)
/// let score = score_overlap("Hello World! More text here", "Hello World!");
/// assert!(score.char_count >= 30);
/// assert!(score.ratio_met);
/// assert!(score.is_safe_boundary);
/// ```
pub(super) fn score_overlap(delta: &str, accumulated: &str) -> OverlapScore {
    let overlap_len = compute_overlap_len(delta, accumulated);
    let ratio_met = compute_ratio_met(delta, overlap_len);
    let is_safe_boundary = overlap_len > 0 && is_safe_boundary(accumulated, accumulated.len());

    OverlapScore {
        char_count: overlap_len,
        ratio_met,
        is_safe_boundary,
    }
}

/// Compute the overlap length: number of chars from `accumulated` that prefix `delta`.
fn compute_overlap_len(delta: &str, accumulated: &str) -> usize {
    if delta.starts_with(accumulated) {
        accumulated.len()
    } else {
        0
    }
}

/// Check if the overlap ratio meets the minimum threshold using integer arithmetic.
fn compute_ratio_met(delta: &str, overlap_len: usize) -> bool {
    if delta.is_empty() {
        return false;
    }
    // Cross-multiply to avoid floating point: overlap/delta >= MIN_OVERLAP_RATIO_INT/100
    let overlap_scaled = overlap_len.saturating_mul(100);
    let threshold = delta.len().saturating_mul(MIN_OVERLAP_RATIO_INT);
    overlap_scaled >= threshold
}
