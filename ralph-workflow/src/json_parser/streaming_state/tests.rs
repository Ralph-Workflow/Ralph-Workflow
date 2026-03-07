// Tests for streaming state tracking.
//
// This file contains all unit tests for the streaming state module,
// including lifecycle tests, deduplication tests, and metrics tests.

#[cfg(test)]
mod tests {
    use super::{
        snapshot_threshold, snapshot_threshold_from_env_fn, ContentType, StreamingSession,
        DEFAULT_SNAPSHOT_THRESHOLD,
    };

    // Tests for StreamingSession lifecycle and content tracking
    include!("tests/session_tests.rs");

    // Tests for snapshot-as-delta detection methods
    include!("tests/state_tests.rs");

    // Tests for delta contract validation
    include!("tests/contract_tests.rs");
}
