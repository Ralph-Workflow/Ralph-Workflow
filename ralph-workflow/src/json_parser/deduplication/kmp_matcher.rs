// KMP (Knuth-Morris-Pratt) matcher for exact substring matching.
//
// Provides linear-time substring search by precomputing a failure function
// that allows skipping already-matched characters.
//
// This is test-only code used for verification after rolling hash matches.

/// KMP (Knuth-Morris-Pratt) matcher for exact substring matching.
///
/// Provides linear-time substring search by precomputing a failure function
/// that allows skipping already-matched characters.
///
/// # Algorithm
///
/// The failure function (also called "prefix function" or "lps array") stores
/// the length of the longest proper prefix of the pattern that is also a suffix
/// for each position in the pattern.
///
/// # Example
///
/// ```ignore
/// let mut kmp = KMPMatcher::new("abc");
/// let text = "xyzabcuvw";
///
/// if let Some(pos) = kmp.find(text) {
///     // Found "abc" at position 3
/// }
/// ```
#[derive(Debug, Clone)]
#[cfg(test)]
pub struct KMPMatcher {
    /// The pattern to search for
    pattern: String,
    /// Failure function (longest proper prefix which is also suffix)
    failure: Vec<usize>,
}

#[cfg(test)]
impl KMPMatcher {
    /// Create a new KMP matcher for the given pattern.
    ///
    /// Precomputes the failure function for efficient searching.
    ///
    /// # Arguments
    /// * `pattern` - The pattern to search for
    ///
    /// # Example
    /// ```ignore
    /// let matcher = KMPMatcher::new("hello");
    /// ```
    #[must_use] 
    pub fn new(pattern: &str) -> Self {
        let pattern = pattern.to_string();
        let failure = Self::compute_failure(&pattern);
        Self { pattern, failure }
    }

    /// Compute the failure function for KMP.
    ///
    /// The failure function `lps[i]` stores the length of the longest proper
    /// prefix of `pattern[0..=i]` that is also a suffix.
    ///
    /// # Example
    ///
    /// For pattern "abab":
    /// - lps[0] = 0 (no proper prefix for single char)
    /// - lps[1] = 0 ("ab" has no matching prefix/suffix)
    /// - lps[2] = 1 ("aba" has "a" as prefix and suffix)
    /// - lps[3] = 2 ("abab" has "ab" as prefix and suffix)
    fn compute_failure(pattern: &str) -> Vec<usize> {
        let m = pattern.len();
        if m == 0 {
            return Vec::new();
        }

        let mut lps = vec![0; m];
        let mut len = 0; // Length of the previous longest prefix suffix
        let mut i = 1;

        let pattern_bytes = pattern.as_bytes();

        while i < m {
            if pattern_bytes[i] == pattern_bytes[len] {
                len = len.saturating_add(1);
                lps[i] = len;
                i = i.saturating_add(1);
            } else if len != 0 {
                // Fall back to previous longest prefix suffix
                len = lps[len - 1];
                // Note: we don't increment i here
            } else {
                // No proper prefix suffix found
                lps[i] = 0;
                i = i.saturating_add(1);
            }
        }

        lps
    }

    /// Find the pattern in text, returning the first position.
    ///
    /// Uses the precomputed failure function for O(n) search time.
    ///
    /// # Arguments
    /// * `text` - The text to search in
    ///
    /// # Returns
    /// * `Some(position)` - The starting position of the pattern
    /// * `None` - Pattern not found
    ///
    /// # Example
    /// ```ignore
    /// let matcher = KMPMatcher::new("world");
    /// assert_eq!(matcher.find("hello world"), Some(6));
    /// assert_eq!(matcher.find("hello"), None);
    /// ```
    #[must_use] 
    pub fn find(&self, text: &str) -> Option<usize> {
        let n = text.len();
        let m = self.pattern.len();

        if m == 0 || n < m {
            return None;
        }

        let text_bytes = text.as_bytes();
        let pattern_bytes = self.pattern.as_bytes();

        let mut i = 0; // Index for text
        let mut j = 0; // Index for pattern

        while i < n {
            if pattern_bytes[j] == text_bytes[i] {
                i = i.saturating_add(1);
                j = j.saturating_add(1);

                if j == m {
                    // Found complete pattern match
                    return Some(i - j);
                }
            } else if j != 0 {
                // Use failure function to skip ahead
                j = self.failure[j - 1];
            } else {
                // No match at all, move to next character
                i = i.saturating_add(1);
            }
        }

        None
    }

    /// Find all occurrences of the pattern in text.
    ///
    /// Returns all positions where the pattern appears in the text.
    ///
    /// # Arguments
    /// * `text` - The text to search in
    ///
    /// # Returns
    /// A vector of positions where the pattern was found
    ///
    /// # Example
    /// ```ignore
    /// let matcher = KMPMatcher::new("ab");
    /// let positions = matcher.find_all("ababab");
    /// assert_eq!(positions, vec![0, 2, 4]);
    /// ```
    #[cfg(test)]
    #[must_use] 
    pub fn find_all(&self, text: &str) -> Vec<usize> {
        let mut positions = Vec::new();
        let n = text.len();
        let m = self.pattern.len();

        if m == 0 || n < m {
            return positions;
        }

        let text_bytes = text.as_bytes();
        let pattern_bytes = self.pattern.as_bytes();

        let mut i = 0; // Index for text
        let mut j = 0; // Index for pattern

        while i < n {
            if pattern_bytes[j] == text_bytes[i] {
                i = i.saturating_add(1);
                j = j.saturating_add(1);

                if j == m {
                    // Found complete pattern match
                    positions.push(i - j);
                    j = self.failure[j - 1];
                }
            } else if j != 0 {
                j = self.failure[j - 1];
            } else {
                i = i.saturating_add(1);
            }
        }

        positions
    }

    /// Get the pattern length.
    #[cfg(test)]
    #[must_use] 
    pub const fn pattern_len(&self) -> usize {
        self.pattern.len()
    }

    /// Check if the pattern is empty.
    #[cfg(test)]
    #[must_use] 
    pub const fn is_empty(&self) -> bool {
        self.pattern.is_empty()
    }
}
