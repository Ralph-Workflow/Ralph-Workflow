// Rolling hash window implementation for fast substring detection.
//
// Uses the Rabin-Karp algorithm for O(1) hash computation on sliding windows.

use std::collections::HashMap;

/// Rolling hash window for fast substring detection.
///
/// Maintains rolling hash values over accumulated content using the Rabin-Karp
/// algorithm. Provides O(1) hash computation for sliding windows.
///
/// # Algorithm
///
/// Uses polynomial rolling hash with base 256 (byte values) and modulus
/// from a large prime to minimize collisions.
///
/// Hash computation:
/// ```text
/// hash(s[0..n]) = (s[0] * b^(n-1) + s[1] * b^(n-2) + ... + s[n-1]) mod m
/// ```
///
/// Rolling update:
/// ```text
/// hash(s[i+1..i+n+1]) = ((hash(s[i..i+n]) - s[i] * b^(n-1)) * b + s[i+n]) mod m
/// ```
///
/// # Example
///
/// ```ignore
/// let mut window = RollingHashWindow::new();
/// window.add_content("Hello World");
///
/// // Check if "World" exists in the accumulated content
/// let hashes = window.get_window_hashes(5); // 5 = length of "World"
/// let world_hash = RollingHashWindow::compute_hash("World");
///
/// if hashes.contains(&world_hash) {
///     // Potential match - verify with KMP
/// }
/// ```
#[derive(Debug, Default, Clone)]
pub struct RollingHashWindow {
    /// Accumulated content for hash computation
    content: String,
    /// Cached hash values for different window sizes
    /// Maps `window_size` -> Vec<(position, hash)>
    cached_hashes: HashMap<usize, Vec<(usize, u64)>>,
}

impl RollingHashWindow {
    /// Base for polynomial rolling hash (256 for byte values)
    const BASE: u64 = 256;
    /// Modulus for hash computation (large prime to minimize collisions)
    const MODULUS: u64 = 2_147_483_647; // 2^31 - 1 (Mersenne prime)

    /// Create a new rolling hash window.
    #[cfg(test)]
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Compute rolling hash of a string slice.
    ///
    /// Uses polynomial rolling hash with base 256 and a large prime modulus.
    ///
    /// # Arguments
    /// * `text` - The text to hash
    ///
    /// # Returns
    /// The hash value as a u64
    ///
    /// # Example
    /// ```ignore
    /// let hash = RollingHashWindow::compute_hash("Hello");
    /// ```
    #[must_use]
    pub fn compute_hash(text: &str) -> u64 {
        text.bytes().fold(0u64, |hash, byte| {
            (hash * Self::BASE + u64::from(byte)) % Self::MODULUS
        })
    }

    /// Compute base^(n-1) mod MODULUS for rolling hash updates.
    ///
    /// This is used to efficiently remove the leftmost character when
    /// sliding the window.
    #[cfg(test)]
    fn compute_power(power: usize) -> u64 {
        (0..power).fold(1u64, |result, _| (result * Self::BASE) % Self::MODULUS)
    }

    /// Add content to the window and update cached hashes.
    ///
    /// This appends new content and recomputes hash values for all
    /// cached window sizes.
    ///
    /// # Arguments
    /// * `text` - The new content to add
    #[cfg(test)]
    pub fn add_content(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }

        let _start_pos = self.content.len();
        self.content.push_str(text);

        // Invalidate and recompute all cached hashes
        self.cached_hashes.clear();
    }

    /// Get all window hashes of a specific size.
    ///
    /// Computes rolling hash values for all windows of the given size
    /// in the accumulated content.
    ///
    /// # Arguments
    /// * `window_size` - The size of each window
    ///
    /// # Returns
    /// A vector of (position, hash) tuples for each window
    ///
    /// # Example
    /// ```ignore
    /// // Get hashes for all 5-character windows
    /// let hashes = window.get_window_hashes(5);
    /// ```
    #[cfg(test)]
    pub fn get_window_hashes(&mut self, window_size: usize) -> Vec<(usize, u64)> {
        // Return cached copy if available
        if let Some(hashes) = self.cached_hashes.get(&window_size) {
            return hashes.clone();
        }

        // Compute hashes for this window size
        let content_bytes = self.content.as_bytes();
        let content_len = content_bytes.len();

        if content_len < window_size {
            // Not enough content for even one window
            let empty: Vec<(usize, u64)> = Vec::new();
            self.cached_hashes.insert(window_size, empty.clone());
            return empty;
        }

        let mut hashes = Vec::new();
        let mut hash: u64 = 0;

        // Compute initial window hash
        for byte in content_bytes.iter().take(window_size) {
            hash = (hash * Self::BASE + u64::from(*byte)) % Self::MODULUS;
        }
        hashes.push((0, hash));

        // Precompute base^(window_size-1) mod MODULUS for rolling updates
        let power = Self::compute_power(window_size - 1);

        // Slide window and compute rolling hashes
        for i in 1..=(content_len - window_size) {
            // Remove leftmost character contribution
            let leftmost = u64::from(content_bytes[i - 1]);
            let removed = (leftmost * power) % Self::MODULUS;
            hash = (hash + Self::MODULUS - removed) % Self::MODULUS;

            // Shift and add new character
            hash = (hash * Self::BASE) % Self::MODULUS;
            let new_char = u64::from(content_bytes[i + window_size - 1]);
            hash = (hash + new_char) % Self::MODULUS;

            hashes.push((i, hash));
        }

        // Cache for future use
        self.cached_hashes.insert(window_size, hashes.clone());
        hashes
    }

    /// Check if a hash exists in any window of the given size.
    ///
    /// This is a fast O(1) check after hashes have been computed.
    ///
    /// # Arguments
    /// * `hash` - The hash value to search for
    /// * `window_size` - The window size to check
    ///
    /// # Returns
    /// * `Some(position)` - The position where the hash was found
    /// * `None` - Hash not found
    #[cfg(test)]
    pub fn contains_hash(&mut self, hash: u64, window_size: usize) -> Option<usize> {
        let hashes = self.get_window_hashes(window_size);
        hashes
            .into_iter()
            .find(|(_, h)| *h == hash)
            .map(|(pos, _)| pos)
    }

    /// Clear all content and cached hashes.
    pub fn clear(&mut self) {
        self.content.clear();
        self.cached_hashes.clear();
    }

    /// Get the current content length.
    #[cfg(test)]
    #[must_use]
    pub const fn len(&self) -> usize {
        self.content.len()
    }

    /// Check if the window is empty.
    #[cfg(test)]
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.content.is_empty()
    }
}
