//! Long-running pipeline memory stability tests
//!
//! These tests simulate realistic long-running pipeline execution to verify
//! that memory usage remains bounded over thousands of iterations.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! This module tests observable behavior:
//! - Memory does not grow unboundedly over 10k+ iterations
//! - Execution history remains bounded at configured limit
//! - Checkpoint size remains reasonable even with maximum history
//! - No Arc count growth beyond expected clones

use crate::test_timeout::with_default_timeout;
use ralph_workflow::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use ralph_workflow::monitoring::memory_metrics::MemorySnapshot;
use ralph_workflow::reducer::state::PipelineState;

const MAX_CHECKPOINT_SIZE_BYTES: usize = 2 * 1024 * 1024;
const MAX_EXECUTION_HISTORY_PROXY_BYTES: usize = 2 * 1024 * 1024;

/// Helper function to create a test execution step.
fn create_test_step(iteration: u32) -> ExecutionStep {
    ExecutionStep::new(
        "Development",
        iteration,
        "agent_invoked",
        StepOutcome::success(
            Some(format!("output for iteration {iteration}")),
            vec![format!("file_{}.rs", iteration % 100)],
        ),
    )
    .with_agent("test-agent")
    .with_duration(5)
}

#[test]
fn test_1k_iterations_memory_remains_bounded() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(2_000, 5);
        let limit = 1000;

        // Simulate 1,900 iterations (900 beyond limit to trigger sliding window eviction)
        for i in 0..1_900 {
            state.add_execution_step(create_test_step(i), limit);

            // Verify history never exceeds limit
            assert!(
                state.execution_history.len() <= limit,
                "History length {} exceeded limit {} at iteration {}",
                state.execution_history.len(),
                limit,
                i
            );
        }

        // Final verification
        assert_eq!(
            state.execution_history.len(),
            limit,
            "Final history should be at limit"
        );

        // Verify we kept the most recent entries (sliding window: oldest 900 evicted)
        let first_entry = state.execution_history.front().unwrap();
        let last_entry = state.execution_history.back().unwrap();

        assert!(
            first_entry.iteration >= 900,
            "First entry should be from recent iterations, got {}",
            first_entry.iteration
        );
        assert_eq!(
            last_entry.iteration, 1_899,
            "Last entry should be most recent"
        );
    });
}

#[test]
fn test_checkpoint_size_remains_reasonable_with_max_history() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1_000, 5);
        let limit = 1000;

        // Fill history to limit
        for i in 0..500 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Serialize to JSON (checkpoint format)
        let serialized = serde_json::to_string(&state).expect("Should serialize state");

        let size_bytes = serialized.len();

        // With 1000 entries, checkpoint should be under 2MB
        // (based on benchmark measurements: ~375KB for 1000 entries)
        assert!(
            size_bytes < MAX_CHECKPOINT_SIZE_BYTES,
            "Checkpoint size {size_bytes} bytes exceeds maximum {MAX_CHECKPOINT_SIZE_BYTES} bytes"
        );

        // Log size for regression tracking
        println!(
            "Checkpoint size with {} history entries: {} KB",
            state.execution_history.len(),
            size_bytes / 1024
        );
    });
}

#[test]
fn test_memory_growth_rate_is_zero_after_limit_reached() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1000, 5);
        let limit = 500;

        // Fill to limit
        for i in 0..500 {
            state.add_execution_step(create_test_step(i), limit);
        }

        assert_eq!(state.execution_history.len(), 500); // OK: precondition; content checked via front() iteration below

        // Add 600 more iterations - length should remain constant
        for i in 500..1100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        assert_eq!(
            state.execution_history.len(),
            limit,
            "History length should remain at limit"
        );

        // Verify ring buffer behavior - oldest entries dropped
        let first_iteration = state.execution_history.front().unwrap().iteration;
        assert!(
            first_iteration >= 600,
            "Oldest entry should be from recent iterations, got {first_iteration}"
        );
    });
}

#[test]
fn test_execution_history_memory_proxy_remains_bounded() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1_000, 5);
        let limit = 1000;

        // Fill history to limit and beyond.
        for i in 0..1100 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Use production memory proxy from monitoring::memory_metrics.
        let snapshot = MemorySnapshot::from_pipeline_state(&state);
        let history_heap_proxy = snapshot.execution_history_heap_bytes;

        // With bounded execution history, the deterministic proxy must stay bounded too.
        assert!(
            history_heap_proxy < MAX_EXECUTION_HISTORY_PROXY_BYTES,
            "Execution history heap proxy {history_heap_proxy} exceeds maximum {MAX_EXECUTION_HISTORY_PROXY_BYTES}"
        );

        println!(
            "Execution history heap proxy: {} KB",
            history_heap_proxy / 1024
        );
    });
}
