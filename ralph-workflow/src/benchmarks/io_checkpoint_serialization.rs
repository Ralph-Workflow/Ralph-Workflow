//! Checkpoint serialization performance benchmarks
//!
//! These benchmarks measure serialization/deserialization performance for checkpoints
//! with various state sizes. They establish baseline metrics for:
//! - Serialization time for different history sizes
//! - Checkpoint file size growth
//! - Deserialization time
//!
//! These are primarily measurement benchmarks (run with `--nocapture` to see output),
//! with a small number of hard invariants to prevent unbounded regression.
//! Run with `--nocapture` to see output.

use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use crate::checkpoint::SizeThresholds;
use crate::reducer::state::PipelineState;
use std::time::Instant;

fn perf_ceiling_asserts_enabled() -> bool {
    // Wall-clock performance varies wildly across CI runners and build profiles.
    // Keep ceilings as an explicit opt-in for dedicated perf jobs.
    std::env::var_os("RALPH_WORKFLOW_PERF_CEILINGS").is_some()
}

/// Helper function to create a test execution step.
fn create_test_step(iteration: u32) -> ExecutionStep {
    ExecutionStep::new(
        "Development",
        iteration,
        "agent_invoked",
        StepOutcome::success(
            Some("Test output from agent".to_string()),
            vec!["src/file1.rs".to_string(), "src/file2.rs".to_string()],
        ),
    )
    .with_agent("test-agent")
    .with_duration(5)
}

/// Create a test pipeline state with N execution history entries.
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
fn benchmark_checkpoint_serialization_empty_state() {
    let state = create_test_pipeline_state(10, 5, 0);
    let start = Instant::now();

    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let duration = start.elapsed();
    let size_bytes = json.len();
    let size_kb = size_bytes / 1024;

    println!("\n=== Checkpoint Serialization (Empty State) ===");
    println!("Serialization time: {duration:?}");
    println!("Checkpoint size: {size_bytes} bytes ({size_kb} KB)");
    println!(
        "Execution history entries: {}",
        state.execution_history_len()
    );

    // Verify serialization works
    assert!(!json.is_empty());

    // Wall-clock sanity checks are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        assert!(
            duration.as_millis() < 1000,
            "Serialization should complete in reasonable time"
        );
    }
}

#[test]
fn benchmark_checkpoint_serialization_small_state() {
    let state = create_test_pipeline_state(10, 5, 10);
    let start = Instant::now();

    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let duration = start.elapsed();
    let size_bytes = json.len();
    let size_kb = size_bytes / 1024;

    println!("\n=== Checkpoint Serialization (Small State - 10 steps) ===");
    println!("Serialization time: {duration:?}");
    println!("Checkpoint size: {size_bytes} bytes ({size_kb} KB)");
    println!(
        "Execution history entries: {}",
        state.execution_history_len()
    );
    println!(
        "Bytes per history entry: ~{}",
        size_bytes / state.execution_history_len().max(1)
    );

    // Verify serialization works
    assert_eq!(state.execution_history_len(), 10);

    // Wall-clock sanity checks are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        assert!(
            duration.as_millis() < 1000,
            "Serialization should complete in reasonable time"
        );
    }
}

#[test]
fn benchmark_checkpoint_serialization_medium_state() {
    let state = create_test_pipeline_state(100, 20, 100);
    let start = Instant::now();

    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let duration = start.elapsed();
    let size_bytes = json.len();
    let size_kb = size_bytes / 1024;

    println!("\n=== Checkpoint Serialization (Medium State - 100 steps) ===");
    println!("Serialization time: {duration:?}");
    println!("Checkpoint size: {size_bytes} bytes ({size_kb} KB)");
    println!(
        "Execution history entries: {}",
        state.execution_history_len()
    );
    println!(
        "Bytes per history entry: ~{}",
        size_bytes / state.execution_history_len()
    );

    // Verify serialization works
    assert_eq!(state.execution_history_len(), 100);

    // This establishes baseline - may be slow initially
    // After bounding implementation (step 11), should improve
}

#[test]
fn benchmark_checkpoint_serialization_large_state() {
    let state = create_test_pipeline_state(100, 20, 1000);
    let start = Instant::now();

    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let duration = start.elapsed();
    let size_bytes = json.len();
    let checkpoint_kb = size_bytes / 1024;
    let checkpoint_megabytes = checkpoint_kb / 1024;

    println!("\n=== Checkpoint Serialization (Large State - 1000 steps) ===");
    println!("Serialization time: {duration:?}");
    println!("Checkpoint size: {size_bytes} bytes ({checkpoint_kb} KB, {checkpoint_megabytes} MB)");
    println!(
        "Execution history entries: {}",
        state.execution_history_len()
    );
    println!(
        "Bytes per history entry: ~{}",
        size_bytes / state.execution_history_len()
    );

    // Verify serialization works
    assert_eq!(state.execution_history_len(), 1000);

    // This demonstrates serialization performance with large history
    // After bounding implementation, history will be capped at limit (default 1000)
}

#[test]
fn benchmark_checkpoint_deserialization_small_state() {
    let state = create_test_pipeline_state(10, 5, 10);
    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let start = Instant::now();
    let deserialized: PipelineState =
        serde_json::from_str(&json).expect("Deserialization should succeed");
    let duration = start.elapsed();

    println!("\n=== Checkpoint Deserialization (Small State - 10 steps) ===");
    println!("Deserialization time: {duration:?}");
    println!("Checkpoint size: {} bytes", json.len());
    println!(
        "Execution history entries: {}",
        deserialized.execution_history_len()
    );

    // Verify deserialization works correctly
    assert_eq!(deserialized.execution_history_len(), 10);

    // Wall-clock sanity checks are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        assert!(
            duration.as_millis() < 1000,
            "Deserialization should complete in reasonable time"
        );
    }
}

#[test]
fn benchmark_checkpoint_deserialization_large_state() {
    let state = create_test_pipeline_state(100, 20, 1000);
    let json = serde_json::to_string(&state).expect("Serialization should succeed");

    let start = Instant::now();
    let deserialized: PipelineState =
        serde_json::from_str(&json).expect("Deserialization should succeed");
    let duration = start.elapsed();

    let size_kb = json.len() / 1024;

    println!("\n=== Checkpoint Deserialization (Large State - 1000 steps) ===");
    println!("Deserialization time: {duration:?}");
    println!("Checkpoint size: {size_kb} KB");
    println!(
        "Execution history entries: {}",
        deserialized.execution_history_len()
    );

    // Verify deserialization works correctly
    assert_eq!(deserialized.execution_history_len(), 1000);
}

#[test]
fn benchmark_checkpoint_round_trip() {
    let original = create_test_pipeline_state(50, 10, 100);

    let serialize_start = Instant::now();
    let json = serde_json::to_string(&original).expect("Serialization should succeed");
    let serialize_duration = serialize_start.elapsed();

    let deserialize_start = Instant::now();
    let restored: PipelineState =
        serde_json::from_str(&json).expect("Deserialization should succeed");
    let deserialize_duration = deserialize_start.elapsed();

    let total_duration = serialize_duration + deserialize_duration;
    let size_kb = json.len() / 1024;

    println!("\n=== Checkpoint Round Trip (100 steps) ===");
    println!("Serialize time: {serialize_duration:?}");
    println!("Deserialize time: {deserialize_duration:?}");
    println!("Total time: {total_duration:?}");
    println!("Checkpoint size: {size_kb} KB");
    println!(
        "Execution history entries: {}",
        restored.execution_history_len()
    );

    // Verify round-trip correctness
    assert_eq!(
        restored.execution_history_len(),
        original.execution_history_len()
    );
    assert_eq!(restored.iteration, original.iteration);
    assert_eq!(restored.phase, original.phase);
}

#[test]
fn benchmark_serialization_scaling() {
    let sizes = vec![10, 50, 100, 500, 1000];
    let mut results = Vec::new();

    for size in &sizes {
        let state = create_test_pipeline_state(100, 20, *size);

        let start = Instant::now();
        let json = serde_json::to_string(&state).expect("Serialization should succeed");
        let duration = start.elapsed();

        let size_kb = json.len() / 1024;
        results.push((*size, duration, size_kb));
    }

    println!("\n=== Serialization Scaling ===");
    println!("History Size | Serialize Time | Checkpoint Size");
    println!("-------------|----------------|----------------");

    for (size, duration, kb) in &results {
        println!("{size:12} | {duration:14?} | {kb:12} KB");
    }

    // Verify we tested all sizes
    assert_eq!(results.len(), sizes.len());

    // This demonstrates how serialization time scales with history size
    // Helps identify if there are performance cliffs at certain sizes
}

#[test]
fn benchmark_serialization_performance_ceiling() {
    // This test establishes a performance ceiling for bounded history
    // If this test starts failing, it indicates a performance regression
    let state = create_test_pipeline_state(100, 20, 1000);

    let start = Instant::now();
    let json = serde_json::to_string(&state).unwrap();
    let duration = start.elapsed();

    let size_kb = json.len() / 1024;

    println!("\n=== Serialization Performance Ceiling ===");
    println!("Duration: {duration:?}");
    println!("Size: {size_kb} KB");

    // With bounded history (1000 entries), serialization is expected to be fast.
    // Wall-clock ceilings are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        assert!(
            duration.as_millis() < 100,
            "Serialization performance regression detected: {duration:?} exceeds 100ms ceiling"
        );
    }

    // Checkpoint size must remain under the hard safety limit.
    let hard_limit_bytes = SizeThresholds::DEFAULT.error_threshold;
    assert!(
        json.len() < hard_limit_bytes,
        "Checkpoint size regression detected: {} bytes exceeds hard limit {} bytes",
        json.len(),
        hard_limit_bytes
    );

    // Stricter (1 MiB) size ceilings are opt-in to avoid brittle failures as PipelineState evolves.
    if perf_ceiling_asserts_enabled() {
        const ONE_MIB: usize = 1024 * 1024;
        assert!(
            json.len() < ONE_MIB,
            "Checkpoint size regression detected: {size_kb} KB exceeds 1 MiB ceiling"
        );
    }
}

#[test]
fn benchmark_deserialization_performance_ceiling() {
    // Companion test to serialization ceiling - verifies deserialization performance
    let state = create_test_pipeline_state(100, 20, 1000);
    let json = serde_json::to_string(&state).unwrap();

    let start = Instant::now();
    let _restored: PipelineState = serde_json::from_str(&json).unwrap();
    let duration = start.elapsed();

    let size_kb = json.len() / 1024;

    println!("\n=== Deserialization Performance Ceiling ===");
    println!("Duration: {duration:?}");
    println!("Size: {size_kb} KB");

    // Wall-clock ceilings are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        assert!(
            duration.as_millis() < 100,
            "Deserialization performance regression detected: {duration:?} exceeds 100ms ceiling"
        );
    }
}

#[test]
fn benchmark_round_trip_performance_ceiling() {
    // Verifies total checkpoint cycle (save + restore) performance
    let original = create_test_pipeline_state(100, 20, 1000);

    let serialize_start = Instant::now();
    let json = serde_json::to_string(&original).unwrap();
    let serialize_duration = serialize_start.elapsed();

    let deserialize_start = Instant::now();
    let _restored: PipelineState = serde_json::from_str(&json).unwrap();
    let deserialize_duration = deserialize_start.elapsed();

    let total_duration = serialize_duration + deserialize_duration;
    let size_kb = json.len() / 1024;

    println!("\n=== Round Trip Performance Ceiling ===");
    println!("Serialize: {serialize_duration:?}");
    println!("Deserialize: {deserialize_duration:?}");
    println!("Total: {total_duration:?}");
    println!("Size: {size_kb} KB");

    // Wall-clock ceilings are opt-in to avoid flaky failures on noisy CI hosts.
    if perf_ceiling_asserts_enabled() {
        // Total round-trip should be under 200ms with bounded history
        assert!(
            total_duration.as_millis() < 200,
            "Round-trip performance regression detected: {total_duration:?} exceeds 200ms ceiling"
        );
    }
}
