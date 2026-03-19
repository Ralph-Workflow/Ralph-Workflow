//! Memory usage benchmarks
//!
//! These benchmarks measure memory growth patterns during pipeline execution.
//! They establish baseline metrics for:
//! - Execution history growth rate
//! - Pipeline state size over iterations
//! - Memory usage per iteration
//!
//! **These are measurement benchmarks, not pass/fail tests.**
//! Run with `--nocapture` to see output.

use crate::benchmarks::io_baselines::estimate_execution_step_heap_bytes_core_fields;
use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use crate::reducer::state::PipelineState;
use std::time::Instant;

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
fn benchmark_execution_history_growth_10_iterations() {
    let mut state = PipelineState::initial(100, 5);
    let start_size = std::mem::size_of_val(state.execution_history());

    // Simulate 10 iterations
    for i in 0..10 {
        state.add_execution_step(create_test_step(i), 10);
    }

    let end_size = std::mem::size_of_val(state.execution_history());
    let actual_heap_size: usize = state
        .execution_history()
        .iter()
        .map(estimate_execution_step_heap_bytes_core_fields)
        .sum();

    let growth_per_iteration = if state.execution_history().is_empty() {
        0
    } else {
        actual_heap_size / state.execution_history_len()
    };

    println!("\n=== Execution History Growth (10 iterations) ===");
    println!("Stack size start: {start_size} bytes");
    println!("Stack size end: {end_size} bytes");
    println!("Heap size (estimated): {actual_heap_size} bytes");
    println!("Growth per iteration: ~{growth_per_iteration} bytes");
    println!("Total entries: {}", state.execution_history_len());

    // Document baseline - this is NOT a failure, just measurement
    assert_eq!(state.execution_history_len(), 10);
}

#[test]
fn benchmark_execution_history_growth_100_iterations() {
    let mut state = PipelineState::initial(100, 5);
    let start = Instant::now();

    // Simulate 100 iterations
    for i in 0..100 {
        state.add_execution_step(create_test_step(i), 100);
    }

    let duration = start.elapsed();
    let actual_heap_size: usize = state
        .execution_history()
        .iter()
        .map(estimate_execution_step_heap_bytes_core_fields)
        .sum();

    let growth_per_iteration = if state.execution_history().is_empty() {
        0
    } else {
        actual_heap_size / state.execution_history_len()
    };

    println!("\n=== Execution History Growth (100 iterations) ===");
    println!("Heap size (estimated): {actual_heap_size} bytes");
    println!("Growth per iteration: ~{growth_per_iteration} bytes");
    println!("Total entries: {}", state.execution_history_len());
    println!("Time to populate: {duration:?}");

    // Document baseline
    assert_eq!(state.execution_history_len(), 100);

    // This demonstrates unbounded growth - after step 11 implementation,
    // this should be bounded to configured limit (default 1000)
}

#[test]
fn benchmark_execution_history_growth_1000_iterations() {
    let mut state = PipelineState::initial(1000, 5);
    let start = Instant::now();

    // Simulate 1000 iterations (stress test)
    for i in 0..1000 {
        state.add_execution_step(create_test_step(i), 1000);
    }

    let duration = start.elapsed();
    let actual_heap_size: usize = state
        .execution_history()
        .iter()
        .map(estimate_execution_step_heap_bytes_core_fields)
        .sum();

    let growth_per_iteration = if state.execution_history().is_empty() {
        0
    } else {
        actual_heap_size / state.execution_history_len()
    };
    let heap_kb = actual_heap_size / 1024;
    let heap_megabytes = heap_kb / 1024;

    println!("\n=== Execution History Growth (1000 iterations) ===");
    println!("Heap size (estimated): {actual_heap_size} bytes ({heap_kb} KB, {heap_megabytes} MB)");
    println!("Growth per iteration: ~{growth_per_iteration} bytes");
    println!("Total entries: {}", state.execution_history_len());
    println!("Time to populate: {duration:?}");

    // Document baseline
    assert_eq!(state.execution_history_len(), 1000);

    // This demonstrates the scale of unbounded growth.
    // After step 11 implementation with default limit of 1000,
    // this should show bounded behavior with oldest entries dropped.
}

#[test]
fn benchmark_pipeline_state_size_empty() {
    let state = PipelineState::initial(100, 5);

    // Approximate size calculation
    let base_size = std::mem::size_of_val(&state);
    let execution_history_size = std::mem::size_of_val(state.execution_history());

    println!("\n=== Pipeline State Size (Empty) ===");
    println!("Base state size: {base_size} bytes");
    println!("Execution history Vec size: {execution_history_size} bytes");
    println!(
        "Total execution history entries: {}",
        state.execution_history_len()
    );

    // Baseline measurement
    assert!(state.execution_history().is_empty());
}

#[test]
fn benchmark_pipeline_state_size_with_100_steps() {
    let mut state = PipelineState::initial(100, 5);

    // Add 100 execution steps
    for i in 0..100 {
        state.add_execution_step(create_test_step(i), 100);
    }

    let base_size = std::mem::size_of_val(&state);
    let execution_history_size = std::mem::size_of_val(state.execution_history());
    let heap_size: usize = state
        .execution_history()
        .iter()
        .map(estimate_execution_step_heap_bytes_core_fields)
        .sum();

    println!("\n=== Pipeline State Size (100 steps) ===");
    println!("Base state size: {base_size} bytes");
    println!("Execution history Vec size: {execution_history_size} bytes");
    println!("Execution history heap size: ~{heap_size} bytes");
    println!("Total size estimate: ~{} bytes", base_size + heap_size);
    println!(
        "Total execution history entries: {}",
        state.execution_history_len()
    );

    // Baseline measurement
    assert_eq!(state.execution_history_len(), 100);
}

#[test]
fn benchmark_memory_growth_rate() {
    let mut state = PipelineState::initial(1000, 5);
    let mut sizes = Vec::new();

    // Measure growth at intervals
    for i in 0..1000 {
        state.add_execution_step(create_test_step(i), 1000);

        // Sample every 100 iterations
        if (i + 1) % 100 == 0 {
            let heap_size: usize = state
                .execution_history()
                .iter()
                .map(|step| {
                    step.phase.len()
                        + step.step_type.len()
                        + step.timestamp.len()
                        + step.agent.as_ref().map_or(0, |s| s.len())
                })
                .sum();
            sizes.push((i + 1, heap_size));
        }
    }

    println!("\n=== Memory Growth Rate ===");
    println!("Iterations | Heap Size (KB) | Growth from prev");
    println!("-----------|----------------|------------------");

    for (idx, (iter, size)) in sizes.iter().enumerate() {
        let size_kb = size / 1024;
        let growth = if idx > 0 {
            let prev_size = sizes[idx - 1].1;
            let growth_kb = (size - prev_size) / 1024;
            format!("+{growth_kb} KB")
        } else {
            String::from("baseline")
        };
        println!("{iter:10} | {size_kb:14} | {growth}");
    }

    // Verify we have the expected samples
    assert_eq!(sizes.len(), 10); // 1000 / 100 = 10 samples

    // This demonstrates linear growth with unbounded Vec
    // After step 11 implementation, growth should plateau at the limit
}

#[test]
fn benchmark_checkpoint_cycle_memory_stability() {
    // Measures memory stability across 50 checkpoint save/restore cycles
    // Should show no growth after initial allocation
    let mut states = Vec::new();

    for cycle in 0..50 {
        let state = create_test_pipeline_state(100, 5, 1000);
        let json = serde_json::to_string(&state).unwrap();
        let _restored: PipelineState = serde_json::from_str(&json).unwrap();

        if cycle % 10 == 0 {
            states.push((cycle, json.len()));
        }
    }

    // Document memory stability across cycles
    println!("\n=== Checkpoint Cycle Memory Stability ===");
    for (cycle, size) in &states {
        println!("Cycle {:2}: {} KB", cycle, size / 1024);
    }

    // Verify all sizes are similar (no growth)
    let first_size = states[0].1;
    for (cycle, size) in &states {
        // Use signed arithmetic to avoid usize underflow when a later cycle
        // happens to serialize slightly smaller than the first.
        // Use integer arithmetic to compute percentage difference
        // (multiplied by 100 to get whole-number percentage)
        let diff_abs = size.abs_diff(first_size);
        let diff_pct_100x = (diff_abs * 10000) / first_size; // pct * 100
        assert!(
            diff_pct_100x < 500, // 5.0% * 100
            "Cycle {cycle} size should be within 5% of initial: {} KB vs {} KB ({}.{:02}% diff)",
            size / 1024,
            first_size / 1024,
            diff_pct_100x / 100,
            diff_pct_100x % 100,
        );
    }
}

/// Helper function to create a test pipeline state with N execution history entries.
fn create_test_pipeline_state(
    iterations: u32,
    review_passes: u32,
    history_size: usize,
) -> PipelineState {
    let mut state = PipelineState::initial(iterations, review_passes);

    for i in 0..history_size {
        state.add_execution_step(
            create_test_step(u32::try_from(i).expect("index fits in u32")),
            history_size,
        );
    }

    state
}

#[test]
fn benchmark_peak_memory_usage_during_large_state_serialization() {
    // Measure peak memory usage during serialization of large state
    // This helps identify if serialization creates temporary allocations

    let state = create_test_pipeline_state(100, 20, 2000);

    let heap_before: usize = state
        .execution_history()
        .iter()
        .map(|step| {
            step.phase.len()
                + step.step_type.len()
                + step.timestamp.len()
                + step.agent.as_ref().map_or(0, |s| s.len())
        })
        .sum();

    println!("\n=== Peak Memory During Serialization ===");
    println!("Heap size before serialization: {} KB", heap_before / 1024);

    let start = Instant::now();
    let json = serde_json::to_string(&state).unwrap();
    let duration = start.elapsed();

    let json_size = json.len();

    println!("Serialization time: {duration:?}");
    println!("Serialized size: {} KB", json_size / 1024);
    // Integer-based ratio display to avoid usize->f64 precision loss
    let ratio_100x = (json_size * 100) / heap_before;
    println!(
        "Memory overhead ratio: {}.{:02}x",
        ratio_100x / 100,
        ratio_100x % 100,
    );

    // Document baseline
    assert_eq!(state.execution_history_len(), 2000);
}

#[test]
fn benchmark_memory_usage_with_different_history_limits() {
    // Compare memory usage with different history limit configurations
    let limits = vec![100, 500, 1000, 2000];

    println!("\n=== Memory Usage by History Limit ===");
    println!("Limit | Heap Size | Checkpoint Size | Per Entry");
    println!("------|-----------|-----------------|----------");

    for limit in limits {
        let state = create_test_pipeline_state(100, 20, limit);

        let heap_size: usize = state
            .execution_history()
            .iter()
            .map(|step| {
                step.phase.len()
                    + step.step_type.len()
                    + step.timestamp.len()
                    + step.agent.as_ref().map_or(0, |s| s.len())
            })
            .sum();

        let json = serde_json::to_string(&state).unwrap();
        let checkpoint_size = json.len();

        let per_entry_heap = heap_size / limit;
        let per_entry_checkpoint = checkpoint_size / limit;

        println!(
            "{:5} | {:9} | {:15} | heap:{:4} ckpt:{:4}",
            limit,
            format!("{} KB", heap_size / 1024),
            format!("{} KB", checkpoint_size / 1024),
            per_entry_heap,
            per_entry_checkpoint
        );
    }
}
