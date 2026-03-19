// Streaming behavior tests for JSON parsers.
//
// This module contains tests for streaming functionality, event classification,
// health monitoring, session management, and deduplication logic.

// Tests for format_unknown_json_event and event classification
include!("io_streaming/event_tests.rs");

// Tests for streaming session management and snapshot-as-delta detection
include!("io_streaming/session_tests.rs");

// End-to-end streaming integration tests
include!("io_streaming/integration_tests.rs");

// Tests for render deduplication, session-level deduplication, and delta-level deduplication
include!("io_streaming/dedup_render.rs");
include!("io_streaming/dedup_session.rs");
include!("io_streaming/delta_hash_dedup.rs");
include!("io_streaming/ccs_glm_scenarios.rs");
include!("io_streaming/consecutive_duplicate_detection.rs");
include!("io_streaming/result_error_suppression.rs");
