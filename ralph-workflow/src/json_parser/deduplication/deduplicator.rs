// Delta deduplicator implementation.
//
// Orchestrates rolling hash and KMP for two-phase deduplication.

#[cfg(test)]
use crate::json_parser::deduplication::kmp_matcher::KMPMatcher;
use crate::json_parser::deduplication::rolling_hash::RollingHashWindow;

/// Delta deduplicator using rolling hash and KMP.
///
/// Orchestrates the two-phase deduplication approach:
/// 1. Rolling hash for fast filtering
/// 2. KMP for exact verification
///
/// # Example
///
/// ```ignore
/// let mut dedup = DeltaDeduplicator::new();
///
/// // Add accumulated content
/// dedup.add_accumulated("Hello World");
///
/// // Check if a delta is a duplicate
/// if let Some(new_portion) = dedup.extract_new_content("Hello World!") {
///     // "!" is the new portion
///     assert_eq!(new_portion, "!");
/// }
/// ```
#[derive(Debug, Default, Clone)]
pub struct DeltaDeduplicator {
    /// Rolling hash window for accumulated content
    hash_window: RollingHashWindow,
}

impl DeltaDeduplicator {
    /// Create a new delta deduplicator.
    #[cfg(test)]
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Add accumulated content for deduplication tracking.
    ///
    /// # Arguments
    /// * `content` - The accumulated content to track
    #[cfg(test)]
    pub fn add_accumulated(&mut self, content: &str) {
        self.hash_window.add_content(content);
    }

    /// Extract new content from a potential snapshot.
    ///
    /// Uses rolling hash and KMP to detect if the incoming delta contains
    /// previously accumulated content (a snapshot) and extracts only the
    /// new portion.
    ///
    /// # Two-Phase Algorithm
    ///
    /// 1. **Rolling Hash Filter**: Compute hash of delta and check if it exists
    ///    in any window of the accumulated content. O(n) time.
    ///
    /// 2. **KMP Verification**: If hash matches, use KMP to verify actual
    ///    substring match and find the exact position. O(n+m) time.
    ///
    /// # Arguments
    /// * `delta` - The incoming delta to check
    /// * `accumulated` - The previously accumulated content
    ///
    /// # Returns
    /// * `Some(new_portion)` - The delta starts with accumulated, returns new portion only
    /// * `None` - The delta is genuinely new, doesn't start with accumulated
    ///
    /// # Example
    /// ```ignore
    /// let mut dedup = DeltaDeduplicator::new();
    /// dedup.add_accumulated("Hello");
    ///
    /// // Snapshot: "Hello World"
    /// assert_eq!(
    ///     DeltaDeduplicator::extract_new_content("Hello World", "Hello"),
    ///     Some(" World")
    /// );
    ///
    /// // Genuine delta: " World"
    /// assert_eq!(
    ///     DeltaDeduplicator::extract_new_content(" World", "Hello"),
    ///     None
    /// );
    /// ```
    #[cfg(test)]
    #[must_use]
    pub fn extract_new_content<'a>(delta: &'a str, accumulated: &str) -> Option<&'a str> {
        // Handle identical content (delta == accumulated) - return empty string
        if delta == accumulated {
            return Some("");
        }

        // Fast rejection: delta must be longer than accumulated
        if delta.len() <= accumulated.len() {
            return None;
        }

        // Phase 1: Rolling hash check
        let accumulated_hash = RollingHashWindow::compute_hash(accumulated);
        let delta_prefix_hash = RollingHashWindow::compute_hash(&delta[..accumulated.len()]);

        // If hashes don't match, definitely not a snapshot
        if accumulated_hash != delta_prefix_hash {
            return None;
        }

        // Phase 2: KMP verification for exact match
        let kmp = KMPMatcher::new(accumulated);
        if let Some(pos) = kmp.find(delta) {
            // Verify it's at position 0 (snapshot starts with accumulated)
            if pos == 0 {
                return Some(&delta[accumulated.len()..]);
            }
        }

        // Hash collision or match not at start - not a snapshot
        None
    }

    /// Check if delta is likely a snapshot using rolling hash only.
    ///
    /// This is a faster O(n) check that may have false positives due to
    /// hash collisions. Use `extract_new_content` for verified results.
    ///
    /// # Arguments
    /// * `delta` - The incoming delta to check
    /// * `accumulated` - The previously accumulated content
    ///
    /// # Returns
    /// * `true` - Delta may be a snapshot (hash matches)
    /// * `false` - Delta is definitely not a snapshot (hash doesn't match)
    #[must_use]
    pub fn is_likely_snapshot(delta: &str, accumulated: &str) -> bool {
        // Handle identical content (duplicate delta)
        if delta == accumulated {
            return true;
        }

        // Delta must be longer than accumulated to be a snapshot
        if delta.len() <= accumulated.len() {
            return false;
        }

        let accumulated_hash = RollingHashWindow::compute_hash(accumulated);
        let delta_prefix_hash = RollingHashWindow::compute_hash(&delta[..accumulated.len()]);

        accumulated_hash == delta_prefix_hash
    }

    /// Check if delta is likely a snapshot with strong overlap detection.
    ///
    /// This is an enhanced version of `is_likely_snapshot` that applies
    /// strong overlap thresholds to prevent false positives on intentional
    /// repetitions.
    ///
    /// # Strong Overlap Detection
    ///
    /// This method only returns `true` when:
    /// - The overlap meets minimum character count threshold (default: 30 chars)
    /// - The overlap meets minimum ratio threshold (default: 50% of delta)
    /// - The overlap ends at a safe boundary (whitespace/punctuation/newline)
    /// - Short chunks (< 20 chars) are only deduped if exact match
    ///
    /// # Arguments
    /// * `delta` - The incoming delta to check
    /// * `accumulated` - The previously accumulated content
    ///
    /// # Returns
    /// * `true` - Delta is a snapshot meeting strong overlap criteria
    /// * `false` - Delta is either genuine or overlap is too weak
    #[must_use]
    pub fn is_likely_snapshot_with_thresholds(delta: &str, accumulated: &str) -> bool {
        let thresholds = get_overlap_thresholds();

        // Handle short chunks: only dedupe if exact match
        if delta.len() < thresholds.short_chunk_threshold {
            return delta == accumulated;
        }

        // Handle identical content (delta == accumulated)
        // This is a snapshot where no new content is added
        if delta == accumulated {
            return true;
        }

        // Fast rejection: delta must be longer than accumulated
        if delta.len() <= accumulated.len() {
            return false;
        }

        // First check with basic rolling hash for quick rejection
        if !Self::is_likely_snapshot(delta, accumulated) {
            return false;
        }

        // Score the overlap to check if it meets strong overlap criteria
        let score = score_overlap(delta, accumulated);

        // Apply threshold checks
        score.meets_thresholds(&thresholds)
    }

    /// Extract new content from a snapshot with strong overlap detection.
    ///
    /// This is an enhanced version of `extract_new_content` that only extracts
    /// new content when the overlap meets strong overlap thresholds.
    ///
    /// # Arguments
    /// * `delta` - The incoming delta to check
    /// * `accumulated` - The previously accumulated content
    ///
    /// # Returns
    /// * `Some(new_portion)` - The overlap meets thresholds, returns new portion
    /// * `None` - The overlap is too weak or not a snapshot
    #[must_use]
    pub fn extract_new_content_with_thresholds<'a>(
        delta: &'a str,
        accumulated: &str,
    ) -> Option<&'a str> {
        let thresholds = get_overlap_thresholds();

        // Handle short chunks: only dedupe if exact match
        if delta.len() < thresholds.short_chunk_threshold {
            if delta == accumulated {
                return Some("");
            }
            return None;
        }

        // Handle identical content
        if delta == accumulated {
            return Some("");
        }

        // Fast rejection: delta must be longer than accumulated
        if delta.len() <= accumulated.len() {
            return None;
        }

        // Score the overlap
        let score = score_overlap(delta, accumulated);

        // Check if overlap meets thresholds
        if !score.meets_thresholds(&thresholds) {
            return None;
        }

        // Extract new content using the overlap length from the score
        if score.char_count > 0 && delta.len() > score.char_count {
            Some(&delta[score.char_count..])
        } else {
            None
        }
    }

    /// Clear all tracked content.
    pub fn clear(&mut self) {
        self.hash_window.clear();
    }
}
