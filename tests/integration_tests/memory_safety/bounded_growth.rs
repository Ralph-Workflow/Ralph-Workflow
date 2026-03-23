//! Bounded memory growth tests
//!
//! These tests verify that `execution_history` does not grow unbounded during
//! long-running pipelines. Following TDD: these tests are written FIRST and
//! should FAIL until the bounding mechanism is implemented in Step 11.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! This module tests observable behavior:
//! - `execution_history` length remains bounded
//! - Oldest entries are dropped when limit is reached
//! - Checkpoint size remains reasonable with bounded history
//! - Resume from checkpoint preserves bounded history correctly

use crate::test_timeout::with_default_timeout;
use ralph_workflow::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use ralph_workflow::reducer::state::PipelineState;

/// Helper function to create a test execution step.
fn create_test_step(iteration: u32) -> ExecutionStep {
    ExecutionStep::new(
        "Development",
        iteration,
        "agent_invoked",
        StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
    )
    .with_agent("test-agent")
    .with_duration(5)
}

#[test]
fn test_execution_history_does_not_exceed_default_limit() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(2000, 5);
        let limit = 1000; // default limit

        // Simulate 2000 iterations - more than default limit of 1000
        for i in 0..2000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // After bounding implementation, history should be capped at 1000 (default limit)
        assert!(
            state.execution_history.len() <= 1000,
            "Execution history should not exceed default limit of 1000, but got {}",
            state.execution_history.len()
        );

        // Verify most recent entry is preserved (bounded buffer keeps newest)
        let last = state
            .execution_history
            .back()
            .expect("history should not be empty");
        assert_eq!(
            last.iteration, 1999,
            "Most recent entry (iteration 1999) should be preserved"
        );
    });
}

#[test]
fn test_execution_history_drops_oldest_entries_when_limit_reached() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1500, 5);
        let limit = 1000;

        // Add 1500 entries using bounded method
        for i in 0..1500 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // After bounding, history should be at limit (1000)
        assert_eq!(
            state.execution_history.len(),
            1000,
            "History length should equal the limit"
        );

        // Oldest entries (iterations 0-499) should be gone
        // Most recent entries (iterations 500-1499) should remain
        let first_iteration = state
            .execution_history
            .front()
            .map_or(0, |step| step.iteration);

        let last_iteration = state
            .execution_history
            .back()
            .map_or(0, |step| step.iteration);

        // First entry should be from iteration 500 or later (oldest 500 dropped)
        assert!(
            first_iteration >= 500,
            "First entry should be from iteration 500+, got {first_iteration}"
        );

        // Last entry should be from iteration 1499
        assert_eq!(
            last_iteration, 1499,
            "Last entry should be from most recent iteration"
        );
    });
}

#[test]
fn test_execution_history_oldest_entries_replaced_at_exact_limit() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1200, 5);
        let limit = 1000;

        // Fill to limit (1000 entries)
        for i in 0..1000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        assert_eq!(state.execution_history.len(), 1000); // OK: precondition; content checked via front()/back() below

        // Add 200 more entries - should maintain limit by dropping oldest
        for i in 1000..1200 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Should still be at limit
        assert_eq!(
            state.execution_history.len(),
            1000,
            "History should remain at limit after additional entries"
        );

        // First entry should now be from iteration 200 (oldest 200 dropped)
        let first_iteration = state
            .execution_history
            .front()
            .map(|step| step.iteration)
            .unwrap();

        assert_eq!(
            first_iteration, 200,
            "First entry should be from iteration 200 after oldest entries replaced at limit"
        );

        // Last entry should be from iteration 1199
        let last_iteration = state
            .execution_history
            .back()
            .map(|step| step.iteration)
            .unwrap();

        assert_eq!(
            last_iteration, 1199,
            "Last entry should be from most recent iteration"
        );
    });
}

#[test]
fn test_execution_history_bounded_growth_prevents_oom() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(10000, 5);

        // Simulate 10,000 iterations (stress test)
        // Without bounding, this would grow to 10,000 entries
        // With bounding, should stay at limit (1000)
        let limit = 1000;
        for i in 0..10000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // This test verifies the upper bound behavior
        // Before Step 11: This will fail with 10,000 entries (unbounded growth)
        // After Step 11: This will pass with 1,000 entries (bounded)
        let max_allowed = 1000;

        assert!(
            state.execution_history.len() <= max_allowed,
            "Execution history grew unbounded! Expected <= {}, got {}. \
             This test will pass after Step 11 implements bounding.",
            max_allowed,
            state.execution_history.len()
        );

        // Verify most recent entry is preserved (bounded buffer keeps newest)
        let last = state
            .execution_history
            .back()
            .expect("history should not be empty");
        assert_eq!(
            last.iteration, 9999,
            "Most recent entry (iteration 9999) should be preserved after bounding"
        );

        // Verify oldest entries were evicted
        let first = state
            .execution_history
            .front()
            .expect("history should not be empty");
        assert!(
            first.iteration >= 9000,
            "Oldest entries should be evicted; first entry is iteration {}, expected >= 9000",
            first.iteration
        );
    });
}

#[test]
fn test_checkpoint_size_remains_reasonable_with_bounded_history() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(2000, 5);
        let limit = 1000;

        // Add 2000 entries using bounded method
        for i in 0..2000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Serialize to JSON (checkpoint format)
        let json = serde_json::to_string(&state).expect("Serialization should succeed");
        let kilobytes = json.len() / 1024;
        let megabytes = kilobytes / 1024;

        println!(
            "Checkpoint size with {} entries: {} KB ({} MB)",
            state.execution_history.len(),
            kilobytes,
            megabytes
        );

        // With bounded history (1000 entries), checkpoint should be < 1 MB
        // Before bounding: 2000 entries → ~750 KB
        // After bounding: 1000 entries → ~375 KB
        assert!(
            megabytes < 1,
            "Checkpoint size should be < 1 MB with bounded history, got {megabytes} MB"
        );

        // More specific: with 1000 entries, should be around 375-400 KB
        assert!(
            kilobytes < 500,
            "Checkpoint size should be < 500 KB with 1000 entries, got {kilobytes} KB"
        );
    });
}

#[test]
fn test_resume_from_checkpoint_preserves_bounded_history() {
    with_default_timeout(|| {
        let mut original_state = PipelineState::initial(1500, 5);
        let limit = 1000;

        // Add 1500 entries (will be bounded to 1000)
        for i in 0..1500 {
            original_state.add_execution_step(create_test_step(i), limit);
        }

        // Should be bounded
        assert_eq!(original_state.execution_history.len(), 1000); // OK: content checked via front()/back() iteration comparison below

        // Serialize and deserialize (checkpoint round-trip)
        let json = serde_json::to_string(&original_state).expect("Serialization should succeed");
        let restored_state: PipelineState =
            serde_json::from_str(&json).expect("Deserialization should succeed");

        // Restored state should have same bounded history
        assert_eq!(
            restored_state.execution_history.len(),
            1000,
            "Restored state should preserve bounded history length"
        );

        // First and last entries should match
        let original_first = original_state.execution_history.front().unwrap();
        let restored_first = restored_state.execution_history.front().unwrap();
        assert_eq!(
            original_first.iteration, restored_first.iteration,
            "First entry should be preserved after resume"
        );

        let original_last = original_state.execution_history.back().unwrap();
        let restored_last = restored_state.execution_history.back().unwrap();
        assert_eq!(
            original_last.iteration, restored_last.iteration,
            "Last entry should be preserved after resume"
        );
    });
}

#[test]
fn test_bounded_history_maintains_recent_context() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1500, 5);
        let limit = 1000;

        // Add 1500 entries using bounded method
        for i in 0..1500 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Should be bounded to 1000
        assert_eq!(state.execution_history.len(), 1000); // OK: content checked via iteration range assertion below

        // All entries should be from recent iterations (500-1499)
        for step in &state.execution_history {
            assert!(
                step.iteration >= 500,
                "All entries should be from recent iterations, got {}",
                step.iteration
            );
        }

        // Entries should be contiguous (no gaps)
        for i in 1..state.execution_history.len() {
            let prev_iteration = state.execution_history[i - 1].iteration;
            let curr_iteration = state.execution_history[i].iteration;

            assert_eq!(
                curr_iteration,
                prev_iteration + 1,
                "Entries should be contiguous, found gap between {prev_iteration} and {curr_iteration}"
            );
        }
    });
}

#[test]
fn test_execution_history_bounded_with_10000_iterations() {
    with_default_timeout(|| {
        // Stress test: verify history remains bounded over 10,000 iterations
        // This simulates a very long-running pipeline with many development iterations
        let mut state = PipelineState::initial(10000, 5);
        let limit = 1000;

        // Simulate 10,000 iterations
        for i in 0..10000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // History should be bounded to limit, not grow to 10,000 entries
        assert_eq!(
            state.execution_history.len(),
            1000,
            "History should remain at limit even after 10,000 iterations"
        ); // OK: content checked via front()/back() iteration values below

        // First entry should be from iteration 9000 (oldest 9000 dropped)
        let first_iteration = state.execution_history.front().unwrap().iteration;
        assert_eq!(
            first_iteration, 9000,
            "First entry should be from iteration 9000 after dropping oldest 9000"
        );

        // Last entry should be from iteration 9999
        let last_iteration = state.execution_history.back().unwrap().iteration;
        assert_eq!(
            last_iteration, 9999,
            "Last entry should be from most recent iteration"
        );

        // Verify no memory leak: size remains constant
        let json = serde_json::to_string(&state).expect("Serialization should succeed");
        let kilobytes = json.len() / 1024;

        println!("Checkpoint size after 10,000 iterations (bounded to 1000): {kilobytes} KB");

        // Size should be reasonable (< 500 KB) with bounded history
        assert!(
            kilobytes < 500,
            "Checkpoint size should be < 500 KB with bounded history, got {kilobytes} KB"
        );
    });
}

#[test]
fn test_checkpoint_size_remains_stable_with_bounded_history() {
    with_default_timeout(|| {
        // Verify that checkpoint size stabilizes once history reaches limit
        // This tests that there's no gradual memory growth beyond the bound

        let mut state = PipelineState::initial(3000, 5);
        let limit = 1000;

        let mut checkpoint_sizes = Vec::new();

        // Add entries in batches and measure checkpoint size
        for batch in 0..3 {
            let start = batch * 1000;
            let end = (batch + 1) * 1000;

            for i in start..end {
                state.add_execution_step(create_test_step(i), limit);
            }

            let json = serde_json::to_string(&state).expect("Serialization should succeed");
            let kilobytes = json.len() / 1024;
            checkpoint_sizes.push((end, kilobytes));

            println!("After {end} iterations: {kilobytes} KB");
        }

        // After first 1000 iterations, size should be established
        let size_after_1000 = checkpoint_sizes[0].1;

        // After 2000 and 3000 iterations, size should be stable (no growth)
        let size_after_2000 = checkpoint_sizes[1].1;
        let size_after_3000 = checkpoint_sizes[2].1;

        // Sizes should be within 10% of each other (minor variation due to iteration numbers)
        let tolerance = size_after_1000 / 10; // 10% tolerance

        assert!(
            size_after_2000.abs_diff(size_after_1000) <= tolerance,
            "Checkpoint size should remain stable between 1000 and 2000 iterations: {size_after_1000} KB vs {size_after_2000} KB"
        );

        assert!(
            size_after_3000.abs_diff(size_after_1000) <= tolerance,
            "Checkpoint size should remain stable between 1000 and 3000 iterations: {size_after_1000} KB vs {size_after_3000} KB"
        );
    });
}

#[test]
fn test_memory_does_not_grow_with_many_checkpoint_cycles() {
    with_default_timeout(|| {
        // Simulate 100 checkpoint save/restore cycles to verify no accumulation
        // This tests for subtle memory leaks that might occur during serialization

        let limit = 1000;
        let mut state = PipelineState::initial(1000, 5);
        let mut final_states = Vec::new();

        // Fill history once, then repeatedly round-trip the checkpoint payload.
        // Rebuilding the same 1000-entry state on every cycle only adds CPU cost
        // and can push this test over the suite timeout without increasing coverage.
        for i in 0..1000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        let mut checkpoint_json =
            serde_json::to_string(&state).expect("Serialization should succeed");

        for cycle in 0..100 {
            // Store checkpoint size every 10 cycles
            if cycle % 10 == 0 {
                final_states.push((cycle, checkpoint_json.len()));
            }

            let restored: PipelineState =
                serde_json::from_str(&checkpoint_json).expect("Deserialization should succeed");
            checkpoint_json =
                serde_json::to_string(&restored).expect("Serialization should succeed");
        }

        // Verify checkpoint size remains stable across cycles
        let first_size = final_states[0].1;

        for (cycle, size) in &final_states {
            let diff = (*size).abs_diff(first_size);
            let tolerance = first_size / 100; // 1% tolerance

            assert!(
                diff <= tolerance,
                "Checkpoint size should remain stable across cycles. Cycle {cycle}: {size} bytes vs initial {first_size} bytes (diff: {diff} bytes)"
            );
        }

        println!("\n=== Checkpoint Cycle Stability ===");
        for (cycle, size) in &final_states {
            println!("Cycle {}: {} KB", cycle, size / 1024);
        }
    });
}

#[test]
fn test_bounded_growth_with_mixed_phase_operations() {
    with_default_timeout(|| {
        // Test bounded growth across different pipeline phases
        // This simulates realistic pipeline execution with phase transitions

        let mut state = PipelineState::initial(2000, 5);
        let limit = 1000;

        // Simulate mixed operations across phases
        for i in 0..2000 {
            // Vary the phase to simulate realistic pipeline execution
            let phase = match i % 4 {
                0 => "Planning",
                1 => "Development",
                2 => "Review",
                _ => "Commit",
            };

            let step = ExecutionStep::new(
                phase,
                i,
                "agent_invoked",
                StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
            )
            .with_agent("test-agent")
            .with_duration(5);

            state.add_execution_step(step, limit);
        }

        // History should still be bounded
        assert_eq!(
            state.execution_history.len(),
            1000,
            "History should remain at limit despite mixed phase operations"
        );

        // Verify mix of phases is preserved in history
        let planning_count = state
            .execution_history
            .iter()
            .filter(|s| s.phase.as_ref() == "Planning")
            .count();
        let development_count = state
            .execution_history
            .iter()
            .filter(|s| s.phase.as_ref() == "Development")
            .count();
        let review_count = state
            .execution_history
            .iter()
            .filter(|s| s.phase.as_ref() == "Review")
            .count();
        let commit_count = state
            .execution_history
            .iter()
            .filter(|s| s.phase.as_ref() == "Commit")
            .count();

        // Each phase should be represented (roughly 250 each)
        assert!(
            planning_count > 200 && planning_count < 300,
            "Planning phase should be represented: {planning_count}"
        );
        assert!(
            development_count > 200 && development_count < 300,
            "Development phase should be represented: {development_count}"
        );
        assert!(
            review_count > 200 && review_count < 300,
            "Review phase should be represented: {review_count}"
        );
        assert!(
            commit_count > 200 && commit_count < 300,
            "Commit phase should be represented: {commit_count}"
        );

        println!(
            "Phase distribution: Planning={planning_count}, Development={development_count}, Review={review_count}, Commit={commit_count}"
        );
    });
}

#[test]
fn test_execution_history_heap_size_within_baseline() {
    with_default_timeout(|| {
        use ralph_workflow::benchmarks::io_baselines::estimate_execution_step_heap_bytes_core_fields;
        use ralph_workflow::benchmarks::io_baselines::ExecutionHistoryBaseline;

        let mut state = PipelineState::initial(1000, 5);
        let limit = 1000;

        // Fill history to limit
        for i in 0..1000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Measure heap size using the same methodology as the benchmark suite.
        //
        // Intentionally excludes `StepOutcome` payloads to keep this check
        // stable across platforms and small representation changes.
        let heap_size: usize = state
            .execution_history
            .iter()
            .map(estimate_execution_step_heap_bytes_core_fields)
            .sum();

        // Verify against baseline
        let baseline = ExecutionHistoryBaseline::ENTRIES_1000;
        baseline
            .check_heap_size(heap_size)
            .expect("Heap size should be within baseline");

        println!(
            "OK Heap size {} bytes within baseline {} bytes (tolerance {}x)",
            heap_size, baseline.heap_size_bytes, baseline.tolerance
        );
    });
}

#[test]
fn test_checkpoint_serialized_size_within_baseline() {
    with_default_timeout(|| {
        use ralph_workflow::benchmarks::io_baselines::ExecutionHistoryBaseline;

        let mut state = PipelineState::initial(1000, 5);
        let limit = 1000;

        // Fill history to limit
        for i in 0..1000 {
            state.add_execution_step(create_test_step(i), limit);
        }

        // Serialize state
        let serialized = serde_json::to_string(&state).expect("Should serialize");

        let size_bytes = serialized.len();

        // Verify against baseline
        let baseline = ExecutionHistoryBaseline::ENTRIES_1000;
        baseline
            .check_serialized_size(size_bytes)
            .expect("Serialized size should be within baseline");

        println!(
            "OK Serialized size {} bytes within baseline {} bytes (tolerance {}x)",
            size_bytes, baseline.serialized_size_bytes, baseline.tolerance
        );
    });
}
