//! Delta deduplication using KMP and Rolling Hash algorithms.

include!("deduplication/thresholds.rs");

pub mod boundary;

pub mod rolling_hash;

pub mod kmp_matcher;

include!("deduplication/deduplicator.rs");

include!("deduplication/tests.rs");
