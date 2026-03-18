//! Delta deduplication using KMP and Rolling Hash algorithms.
//!
//! This module provides efficient deduplication for streaming deltas using:
//! - **Rolling Hash (Rabin-Karp)**: Fast O(n) filtering to eliminate impossible matches
//! - **KMP (Knuth-Morris-Pratt)**: O(n+m) verification for exact substring matching
//! - **Strong Overlap Detection**: Thresholds and boundary checks to prevent false positives
//!
//! # Enhanced Deduplication
//!
//! The enhanced algorithm uses multiple layers of validation to prevent false positives:
//!
//! 1. **Rolling Hash Filter**: Fast O(n) check to eliminate impossible matches
//! 2. **KMP Verification**: O(n+m) confirmation of actual substring match
//! 3. **Overlap Threshold**: Only dedupe when overlap >= 30 chars AND >= 50% of delta
//! 4. **Boundary Sanity**: Ensure overlap ends at whitespace/punctuation/newline
//! 5. **Short Chunk Protection**: Chunks < 20 chars never deduped unless exact match
//!
//! # Architecture
//!
//! ```text
//! Incoming Delta
//!       │
//!       ▼
//! ┌─────────────────────┐
//! │  Rolling Hash Check │  ◄── Compute hash of delta, compare against
//! │  (Rabin-Karp)       │      sliding window hashes of accumulated content
//! └──────────┬──────────┘
//!            │
//!      ┌──────┴──────┐
//!      │ Hash Match? │
//!      └──────┬──────┘
//!        No   │   Yes
//!        │    │
//!        ▼    ▼
//!     Accept  ┌─────────────────┐
//!     Delta   │  KMP Verification│  ◄── Confirm actual substring match
//!             └────────┬────────┘
//!                      │
//!               ┌──────┴──────┐
//!               │True Match?  │
//!               └──────┬──────┘
//!                 No   │   Yes
//!                 │    │
//!                 ▼    ▼
//!              Accept  ┌─────────────────────┐
//!              Delta   │ Strong Overlap Check│ ◄── >= 30 chars, >= 50%, safe boundary
//!                      └──────────┬────────┘
//!                                 │
//!                          ┌──────┴──────┐
//!                          │Measures?    │
//!                          └──────┬──────┘
//!                            No   │   Yes
//!                            │    │
//!                            ▼    ▼
//!                         Accept  Extract New
//!                         Delta   Portion Only
//! ```

#![allow(forbid_mutating_receiver_methods)]
#![allow(forbid_interior_mutability)]

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
