//! String interning for deduplicating repeated strings in execution history.
//!
//! This module provides a string pool that deduplicates commonly repeated strings
//! (like phase names and agent names) by storing them as Arc<str>. This reduces
//! memory usage when the same strings appear many times across execution history.

use std::collections::HashSet;
use std::sync::Arc;

/// String pool for deduplicating commonly repeated strings in execution history.
///
/// Phase names and agent names are repeated frequently across execution history
/// entries. Using Arc<str> with a string pool reduces memory usage by sharing
/// the same allocation for identical strings.
///
/// # Example
///
/// ```
/// use ralph_workflow::checkpoint::string_pool::StringPool;
/// use std::sync::Arc;
///
/// let mut pool = StringPool::new();
/// let phase1 = pool.intern("Development");
/// let phase2 = pool.intern("Development");
///
/// // Both Arc<str> values point to the same allocation
/// assert!(Arc::ptr_eq(&phase1, &phase2));
/// ```
#[derive(Debug, Clone, Default)]
pub struct StringPool {
    // Store a single allocation per unique string (the Arc payload).
    // Using `Arc<str>` as the set key enables cheap cloning and lookup by `&str`.
    pool: HashSet<Arc<str>>,
}

impl StringPool {
    /// Create a new string pool with default capacity hint.
    ///
    /// Pre-allocates capacity for 16 unique strings, which is typical for
    /// most pipeline runs (phase names, agent names, step types).
    #[must_use]
    pub fn new() -> Self {
        Self::with_capacity(16)
    }

    /// Create a string pool with specific capacity.
    ///
    /// Use this when you know the expected number of unique strings to avoid
    /// hash table resizing during initial population.
    #[must_use]
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            pool: HashSet::with_capacity(capacity),
        }
    }

    /// Get or insert a string slice into the pool, returning an Arc<str>.
    ///
    /// Prefer this when the input is already a `&str` to avoid allocating a
    /// temporary `String` on repeated calls.
    ///
    /// # Example
    ///
    /// ```
    /// use ralph_workflow::checkpoint::string_pool::StringPool;
    ///
    /// let (pool, s1) = StringPool::new().intern_str("test");
    /// let (pool, s2) = pool.intern_str("test");
    /// assert!(std::sync::Arc::ptr_eq(&s1, &s2));
    /// ```
    #[must_use]
    pub fn intern_str(self, s: &str) -> (Self, Arc<str>) {
        if let Some(existing) = self.pool.get(s) {
            return (self, Arc::clone(existing));
        }

        let interned: Arc<str> = Arc::from(s);
        let mut new_pool = self;
        new_pool.pool.insert(Arc::clone(&interned));
        (new_pool, interned)
    }

    /// Get or insert an owned string into the pool, returning an Arc<str>.
    ///
    /// This path can reuse the allocation of the provided `String` when inserting.
    #[must_use]
    pub fn intern_string(self, s: String) -> (Self, Arc<str>) {
        if let Some(existing) = self.pool.get(s.as_str()) {
            return (self, Arc::clone(existing));
        }

        let interned: Arc<str> = Arc::from(s);
        let mut new_pool = self;
        new_pool.pool.insert(Arc::clone(&interned));
        (new_pool, interned)
    }

    /// Backward-compatible convenience: accepts any `Into<String>`.
    ///
    /// This path can reuse the allocation of the provided `String` when inserting.
    pub fn intern_string(&mut self, s: String) -> Arc<str> {
        if let Some(existing) = self.pool.get(s.as_str()) {
            return Arc::clone(existing);
        }

        let interned: Arc<str> = Arc::from(s);
        self.pool.insert(Arc::clone(&interned));
        interned
    }

    /// Backward-compatible convenience: accepts any `Into<String>`.
    ///
    /// Note: callers passing `&str` should prefer `intern_str()` to avoid
    /// allocating a temporary `String` on repeated lookups.
    pub fn intern(&mut self, s: impl Into<String>) -> Arc<str> {
        self.intern_string(s.into())
    }

    /// Get the number of unique strings in the pool.
    #[must_use]
    pub fn len(&self) -> usize {
        self.pool.len()
    }

    /// Check if the pool is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.pool.is_empty()
    }

    /// Clear all entries from the pool.
    #[must_use]
    pub fn clear(self) -> Self {
        Self::with_capacity(16)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_string_pool_new() {
        let pool = StringPool::new();
        assert_eq!(pool.len(), 0);
        assert!(pool.is_empty());
    }

    #[test]
    fn test_string_pool_with_capacity() {
        let pool = StringPool::with_capacity(32);
        assert_eq!(pool.len(), 0);
        assert!(pool.is_empty());
        // Capacity is pre-allocated, so adding strings shouldn't trigger resize
    }

    #[test]
    fn test_identical_strings_return_same_arc() {
        let mut pool = StringPool::new();
        let s1 = pool.intern_str("Development");
        let s2 = pool.intern_str("Development");

        // Both should point to the same allocation
        assert!(Arc::ptr_eq(&s1, &s2));
        assert_eq!(*s1, *s2);
        assert_eq!(pool.len(), 1);
    }

    #[test]
    fn test_different_strings_return_different_arc() {
        let mut pool = StringPool::new();
        let s1 = pool.intern_str("Development");
        let s2 = pool.intern_str("Review");

        // Should point to different allocations
        assert!(!Arc::ptr_eq(&s1, &s2));
        assert_ne!(*s1, *s2);
        assert_eq!(pool.len(), 2);
    }

    #[test]
    fn test_pool_size_does_not_grow_for_repeated_strings() {
        let mut pool = StringPool::new();

        // Intern the same string multiple times
        for _ in 0..100 {
            pool.intern_str("Development");
        }

        // Pool should still only contain one entry
        assert_eq!(pool.len(), 1);
    }

    #[test]
    fn test_intern_different_string_types() {
        let mut pool = StringPool::new();

        // Test with &str
        let s1 = pool.intern_str("test");
        // Test with String
        let s2 = pool.intern("test".to_string());
        // Test with owned String
        let s3 = pool.intern(String::from("test"));

        // All should point to the same allocation
        assert!(Arc::ptr_eq(&s1, &s2));
        assert!(Arc::ptr_eq(&s2, &s3));
        assert_eq!(pool.len(), 1);
    }

    #[test]
    fn test_intern_str_and_intern_string_share_entries() {
        // Regression test: the pool should store a single interned Arc<str> per
        // unique string, regardless of whether callers use &str or String.
        let mut pool = StringPool::new();

        let s1 = pool.intern_str("test");
        let s2 = pool.intern("test".to_string());
        let s3 = pool.intern(String::from("test"));

        assert!(Arc::ptr_eq(&s1, &s2));
        assert!(Arc::ptr_eq(&s2, &s3));
        assert_eq!(pool.len(), 1);
    }

    #[test]
    fn test_clear() {
        let mut pool = StringPool::new();
        pool.intern_str("Development");
        pool.intern_str("Review");
        assert_eq!(pool.len(), 2);

        pool.clear();
        assert_eq!(pool.len(), 0);
        assert!(pool.is_empty());
    }

    #[test]
    fn test_arc_content_matches_input() {
        let mut pool = StringPool::new();
        let arc = pool.intern_str("Development");
        assert_eq!(&*arc, "Development");
    }

    #[test]
    fn test_memory_efficiency_multiple_calls() {
        let mut pool = StringPool::new();
        let mut arcs = Vec::new();

        // Create 1000 references to the same string
        for _ in 0..1000 {
            arcs.push(pool.intern_str("Development"));
        }

        // Pool should still only contain one entry
        assert_eq!(pool.len(), 1);

        // All arcs should point to the same allocation
        for i in 1..arcs.len() {
            assert!(Arc::ptr_eq(&arcs[0], &arcs[i]));
        }
    }

    #[test]
    fn test_empty_string() {
        let mut pool = StringPool::new();
        let s1 = pool.intern_str("");
        let s2 = pool.intern_str("");

        assert!(Arc::ptr_eq(&s1, &s2));
        assert_eq!(&*s1, "");
        assert_eq!(pool.len(), 1);
    }

    #[test]
    fn test_clone_pool() {
        let mut pool = StringPool::new();
        pool.intern_str("Development");
        pool.intern_str("Review");

        let cloned = pool.clone();
        assert_eq!(pool.len(), cloned.len());
    }
}
