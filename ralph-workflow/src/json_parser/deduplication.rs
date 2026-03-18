//! Delta deduplication using KMP and Rolling Hash algorithms.

// Threshold configuration and overlap detection
include!("deduplication/thresholds.rs");

// Rolling hash window for fast substring detection
include!("deduplication/rolling_hash.rs");

// KMP matcher for exact substring verification (test-only)
include!("deduplication/kmp_matcher.rs");

// Delta deduplicator orchestration
include!("deduplication/deduplicator.rs");

// Tests
include!("deduplication/tests.rs");
