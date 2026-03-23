// End-to-end streaming integration tests
//
// This module contains integration tests for streaming functionality,
// split into logical sub-modules:
// - verbose_mode_tests: Tests for verbose mode streaming
// - terminal_mode_tests: Tests for different terminal modes (Full, Basic, None)
// - ccs_glm_tests: Tests for ccs-glm streaming scenarios

// Tests for verbose mode streaming
include!("integration_tests/verbose_mode_tests.rs");

// Tests for different terminal modes (Full, Basic, None)
include!("integration_tests/terminal_mode_tests.rs");

// Tests for ccs-glm streaming scenarios
include!("integration_tests/ccs_glm_tests.rs");
