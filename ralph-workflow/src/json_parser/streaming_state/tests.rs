// Tests for streaming state tracking.
//
// This file contains all unit tests for the streaming state module,
// including lifecycle tests, deduplication tests, and metrics tests.

#[cfg(test)]
mod tests {
    use super::{
        build_mixed_content_reconstruction, build_tool_use_reconstruction,
        compute_content_hash_from_accumulated, compute_hash, extract_delta_from_snapshot,
        is_duplicate_text_content, is_likely_snapshot, merge_delta, snapshot_threshold,
        snapshot_threshold_from_env_fn, sorted_content_keys, ContentType, StreamingSession,
        DEFAULT_SNAPSHOT_THRESHOLD,
    };

    // Tests for StreamingSession lifecycle and content tracking
    include!("io_tests/session_tests.rs");

    // Tests for snapshot-as-delta detection methods
    include!("io_tests/state_tests.rs");

    // Tests for delta contract validation
    include!("io_tests/contract_tests.rs");

    // Tests for pure domain helpers extracted from session.rs
    include!("io_tests/domain_tests.rs");
}
