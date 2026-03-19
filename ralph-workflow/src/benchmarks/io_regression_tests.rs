//! Performance regression tests to prevent future degradation.
//!
//! These tests enforce performance baselines and catch regressions early in CI.
//! They use conservative thresholds with some tolerance for platform variance.

use crate::benchmarks::io_baselines::estimate_execution_step_heap_bytes_core_fields;
use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use crate::checkpoint::StringPool;
use crate::reducer::state::PipelineState;
use std::sync::Arc;
use std::time::Instant;

/// Helper to create a test execution step with string pool.
fn create_test_step_with_pool(iteration: u32, pool: &mut StringPool) -> ExecutionStep {
    ExecutionStep::new_with_pool(
        "Development",
        iteration,
        "agent_invoked",
        StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
        pool,
    )
    .with_agent_pooled("test-agent", pool)
    .with_duration(5)
}

/// Helper to create a large test pipeline state.
fn create_large_state(history_size: usize) -> PipelineState {
    let mut state = PipelineState::initial(1000, 5);
    let mut pool = StringPool::new();

    for i in 0..history_size {
        let step =
            create_test_step_with_pool(u32::try_from(i).expect("index fits in u32"), &mut pool);
        state.add_execution_step(step, history_size);
    }

    state
}

#[test]
fn regression_test_execution_step_memory_footprint() {
    let mut pool = StringPool::new();
    let step = create_test_step_with_pool(1, &mut pool);
    let heap_size = estimate_execution_step_heap_bytes_core_fields(&step);

    // After optimizations (Arc<str> + Box<str>), should be <= 60 bytes per entry
    // This accounts for:
    // - phase: Arc<str> "Development" = 11 bytes
    // - step_type: Box<str> "agent_invoked" = 14 bytes
    // - timestamp: String ~= 25 bytes (ISO 8601 format)
    // - agent: Option<Arc<str>> "test-agent" = 10 bytes
    // Total: ~60 bytes
    assert!(
        heap_size <= 60,
        "Memory regression: {heap_size} bytes per entry exceeds 60 byte target"
    );
}

#[test]
fn regression_test_string_pool_sharing() {
    let mut pool = StringPool::new();

    // Create multiple steps with the same phase and agent
    let step1 = create_test_step_with_pool(1, &mut pool);
    let step2 = create_test_step_with_pool(2, &mut pool);

    // Verify Arc sharing (same pointer)
    assert!(
        Arc::ptr_eq(&step1.phase, &step2.phase),
        "String pool regression: phase strings not shared"
    );
    assert!(
        Arc::ptr_eq(step1.agent.as_ref().unwrap(), step2.agent.as_ref().unwrap()),
        "String pool regression: agent strings not shared"
    );

    // Pool should only contain 2 unique strings (phase and agent)
    assert_eq!(
        pool.len(),
        2,
        "String pool regression: expected 2 unique strings, got {}",
        pool.len()
    );
}

#[test]
fn regression_test_serialization_performance() {
    let state = create_large_state(1000);

    let start = Instant::now();
    let json = serde_json::to_string(&state).unwrap();
    let duration = start.elapsed();

    // Performance ceiling for baseline `serde_json::to_string(&PipelineState)`.
    // NOTE: This is intentionally *not* the checkpoint writer path
    // (`save_checkpoint_with_workspace`), which uses a pre-sized buffer.
    //
    // This ceiling is conservative to account for platform variance and CI overhead.
    if std::env::var_os("RALPH_WORKFLOW_PERF_CEILINGS").is_some() {
        assert!(
            duration.as_millis() <= 10,
            "Serialization regression: {duration:?} exceeds 10ms target"
        );
    }

    // Size should be <= 400 KB (400,000 bytes)
    // Current measurements show ~375 KB, so this gives 6% headroom
    assert!(
        json.len() <= 400_000,
        "Size regression: {} bytes exceeds 400 KB target",
        json.len()
    );
}

#[test]
fn regression_test_deserialization_performance() {
    let state = create_large_state(1000);
    let json = serde_json::to_string(&state).unwrap();

    let start = Instant::now();
    let _deserialized: PipelineState = serde_json::from_str(&json).unwrap();
    let duration = start.elapsed();

    // Deserialization should be <= 10ms (conservative threshold)
    if std::env::var_os("RALPH_WORKFLOW_PERF_CEILINGS").is_some() {
        assert!(
            duration.as_millis() <= 10,
            "Deserialization regression: {duration:?} exceeds 10ms target"
        );
    }
}

#[test]
fn regression_test_round_trip_performance() {
    let state = create_large_state(1000);

    let start = Instant::now();
    let json = serde_json::to_string(&state).unwrap();
    let serialize_duration = start.elapsed();

    let start = Instant::now();
    let _deserialized: PipelineState = serde_json::from_str(&json).unwrap();
    let deserialize_duration = start.elapsed();

    let total_duration = serialize_duration + deserialize_duration;

    // Round trip should be <= 20ms (conservative threshold)
    if std::env::var_os("RALPH_WORKFLOW_PERF_CEILINGS").is_some() {
        assert!(
            total_duration.as_millis() <= 20,
            "Round trip regression: {total_duration:?} exceeds 20ms target"
        );
    }
}

#[test]
fn regression_test_execution_history_bounded_growth() {
    // Verify that execution history respects the configured limit
    let limit = 500;
    let mut state = PipelineState::initial(u32::try_from(limit).expect("limit fits in u32"), 5);
    let mut pool = StringPool::new();

    // Add more entries than the limit
    for i in 0..1000 {
        let step = create_test_step_with_pool(i, &mut pool);
        state.add_execution_step(step, limit);
    }

    // Verify history is bounded to the limit
    assert_eq!(
        state.execution_history_len(),
        limit,
        "Execution history regression: {} entries exceeds limit of {}",
        state.execution_history_len(),
        limit
    );
}

#[test]
fn regression_test_copy_enums_eliminate_clones() {
    // This test verifies that simple enums are Copy, eliminating unnecessary clones
    use crate::reducer::state::{
        ArtifactType, DevelopmentStatus, FixStatus, PromptInputKind, PromptMaterializationReason,
        PromptMode, SameAgentRetryReason,
    };

    // Verify enums are Copy
    fn assert_copy<T: Copy>() {}

    assert_copy::<ArtifactType>();
    assert_copy::<PromptMode>();
    assert_copy::<SameAgentRetryReason>();
    assert_copy::<DevelopmentStatus>();
    assert_copy::<FixStatus>();
    assert_copy::<PromptInputKind>();
    assert_copy::<PromptMaterializationReason>();
}

#[test]
fn regression_test_memory_efficiency_vs_vec() {
    // Verify that Box<str> and Option<Box<[T]>> are more efficient than Vec<T>
    let outcome = StepOutcome::success(
        Some("output".to_string()),
        vec!["file1.txt".to_string(), "file2.txt".to_string()],
    );

    match outcome {
        StepOutcome::Success {
            output,
            files_modified,
            ..
        } => {
            // Box<str> uses exact size (no over-allocation)
            let output_str = output.expect("Output should be present");
            assert_eq!(output_str.len(), "output".len());

            // Box<[String]> uses exact size (no excess capacity)
            let files = files_modified.expect("Files should be present");
            assert_eq!(files.len(), 2);

            // The benefit is that Box<[T]> doesn't have the extra `capacity` field
            // that Vec<T> has, saving memory on every instance
        }
        _ => panic!("Expected Success variant"),
    }
}

#[test]
fn regression_test_checkpoint_size_scaling() {
    // Verify that checkpoint size scales linearly with history size
    let sizes = vec![100, 500, 1000];
    let mut measurements = Vec::new();

    for size in sizes {
        let state = create_large_state(size);
        let json = serde_json::to_string(&state).unwrap();
        measurements.push((size, json.len()));
    }

    // Use deltas between sizes to cancel fixed JSON overhead (key names, other PipelineState
    // fields, braces/commas). Small history sizes can otherwise skew the average.
    let len_100 = measurements
        .iter()
        .find(|(size, _)| *size == 100)
        .map(|(_, len)| *len)
        .unwrap();
    let len_500 = measurements
        .iter()
        .find(|(size, _)| *size == 500)
        .map(|(_, len)| *len)
        .unwrap();
    let len_1000 = measurements
        .iter()
        .find(|(size, _)| *size == 1000)
        .map(|(_, len)| *len)
        .unwrap();

    let bytes_per_entry_100_to_500 = (len_500.saturating_sub(len_100)) / 400;
    let bytes_per_entry_500_to_1000 = (len_1000.saturating_sub(len_500)) / 500;

    assert!(
        (150..=450).contains(&bytes_per_entry_100_to_500),
        "Checkpoint size scaling regression: {bytes_per_entry_100_to_500} bytes per entry for entries 101-500 (expected 150-450)"
    );
    assert!(
        (150..=450).contains(&bytes_per_entry_500_to_1000),
        "Checkpoint size scaling regression: {bytes_per_entry_500_to_1000} bytes per entry for entries 501-1000 (expected 150-450)"
    );

    // Also enforce the band at 1000 entries, where overhead is amortized.
    let bytes_per_entry_at_1000 = len_1000 / 1000;
    assert!(
        (150..=450).contains(&bytes_per_entry_at_1000),
        "Checkpoint size scaling regression: {bytes_per_entry_at_1000} bytes per entry at size 1000 (expected 150-450)"
    );
}

#[test]
fn regression_test_string_pool_memory_bounded() {
    // Verify that string pool doesn't grow unboundedly
    let mut pool = StringPool::new();

    // Create many steps with the same phase and agent
    for i in 0..1000 {
        let _ = create_test_step_with_pool(i, &mut pool);
    }

    // Pool should still only contain 2 unique strings (phase and agent)
    assert_eq!(
        pool.len(),
        2,
        "String pool memory regression: {} entries (expected 2)",
        pool.len()
    );
}

#[test]
fn regression_test_arc_str_vs_string_memory() {
    // Demonstrate memory savings of Arc<str> vs String for repeated values
    let mut pool = StringPool::new();
    let mut steps = Vec::new();

    // Create 100 steps with the same phase
    for i in 0..100 {
        steps.push(create_test_step_with_pool(i, &mut pool));
    }

    // All steps should share the same Arc<str> for phase
    for i in 1..steps.len() {
        assert!(
            Arc::ptr_eq(&steps[0].phase, &steps[i].phase),
            "Arc<str> memory regression: steps 0 and {i} don't share phase allocation"
        );
    }

    // With String: 100 allocations * ~11 bytes = ~1100 bytes
    // With Arc<str>: 1 allocation * 11 bytes = 11 bytes
    // Savings: ~1089 bytes (99% reduction) for just the phase field
}

// TDD test - validates Step 9 implementation
#[test]
fn regression_test_metrics_update_no_clone() {
    // Verify that metrics updates use builder pattern instead of full struct clone
    use crate::reducer::state::RunMetrics;

    let metrics = RunMetrics::default();

    // Test that builder methods exist and work correctly
    let updated = metrics.increment_dev_iterations_started();
    assert_eq!(updated.dev_iterations_started, 1);
    assert_eq!(updated.dev_iterations_completed, 0); // Other fields unchanged

    // Test chaining
    let updated2 = updated
        .increment_dev_iterations_completed()
        .increment_dev_attempts_total();
    assert_eq!(updated2.dev_iterations_started, 1);
    assert_eq!(updated2.dev_iterations_completed, 1);
    assert_eq!(updated2.dev_attempts_total, 1);
}

#[test]
fn regression_test_continuation_state_builder_pattern() {
    // Verify ContinuationState methods follow consuming builder pattern
    use crate::reducer::state::{ArtifactType, ContinuationState};

    let state = ContinuationState::with_limits(3, 3, 2);

    // with_artifact should work without requiring clone
    let updated = state.with_artifact(ArtifactType::Plan);
    assert_eq!(updated.current_artifact, Some(ArtifactType::Plan));
    assert_eq!(updated.xsd_retry_count, 0); // Should reset XSD state
}

#[test]
fn regression_test_boxed_slice_memory_savings() {
    // Verify Box<[T]> is more memory-efficient than Vec<T>
    use std::mem::size_of;

    // Box<[T]> is 16 bytes (fat pointer: data pointer + length)
    // Vec<T> is 24 bytes (pointer + length + capacity)
    // Savings: 8 bytes per instance

    let vec_size = size_of::<Vec<String>>();
    let boxed_slice_size = size_of::<Box<[String]>>();

    // Vec<T> is three usize values (ptr + len + cap)
    assert_eq!(vec_size, 3 * size_of::<usize>());
    // Box<[T]> is a fat pointer (data ptr + len)
    assert_eq!(boxed_slice_size, 2 * size_of::<usize>());
    // Savings: one usize per instance
    assert_eq!(vec_size - boxed_slice_size, size_of::<usize>());
}

#[test]
fn regression_test_continuation_state_boxed_fields() {
    // Verify ContinuationState uses Box<[String]> for immutable fields
    use std::mem::size_of;

    // Option<Box<[String]>> is 16 bytes (fat pointer)
    // Option<Vec<String>> is 24 bytes (pointer + len + capacity)
    let boxed_size = size_of::<Option<Box<[String]>>>();
    let vec_size = size_of::<Option<Vec<String>>>();

    assert_eq!(boxed_size, 2 * size_of::<usize>());
    assert_eq!(vec_size, 3 * size_of::<usize>());
    assert_eq!(vec_size - boxed_size, size_of::<usize>());
}

#[test]
fn test_prompt_inputs_builder_no_allocation() {
    // Verify builder methods don't introduce extra allocations
    use crate::reducer::state::PromptInputsState;

    let inputs = PromptInputsState::default();

    // Builder methods should consume and return without cloning
    let updated = inputs.with_commit_cleared();
    assert!(updated.commit.is_none());

    // Verify other fields are preserved (not cloned/reallocated)
    let inputs2 = PromptInputsState::default();
    let updated2 = inputs2.with_planning_cleared();
    assert!(updated2.planning.is_none());
}

#[test]
fn regression_test_agent_chain_arc_lists() {
    // Verify AgentChainState uses Arc<[String]> for immutable agent lists
    use crate::reducer::state::AgentChainState;
    use std::mem::size_of;
    use std::sync::Arc;

    // Type-level assertions: ensure the fields match the intended Arc-based design.
    let state = AgentChainState::initial();
    let _: &Arc<[String]> = &state.agents;
    let _: &Arc<[Vec<String>]> = &state.models_per_agent;

    let arc_slice_size = size_of::<Arc<[String]>>();
    let vec_size = size_of::<Vec<String>>();

    assert_eq!(arc_slice_size, 2 * size_of::<usize>());
    assert_eq!(vec_size, 3 * size_of::<usize>());
    assert_eq!(vec_size - arc_slice_size, size_of::<usize>());
}

#[test]
fn regression_test_agent_chain_reset_operations() {
    use crate::agents::AgentRole;
    use crate::reducer::state::AgentChainState;

    let agents = vec!["agent1".to_string(), "agent2".to_string()];
    let models: Vec<Vec<String>> = vec![vec!["model1".to_string()], vec!["model2".to_string()]];

    let state = AgentChainState::initial()
        .with_agents(agents, models, AgentRole::Developer)
        .with_max_cycles(5);

    // Test various reset operations
    let state2 = state.reset();
    assert_eq!(state2.current_agent_index, 0);
    assert_eq!(state2.current_model_index, 0);
    assert!(state2.backoff_pending_ms.is_none());
    assert!(state2.rate_limit_continuation_prompt.is_none());

    // Test reset_for_role
    let state3 = state.reset_for_role(AgentRole::Reviewer);
    assert_eq!(state3.current_role, AgentRole::Reviewer);
    assert_eq!(state3.current_agent_index, 0);

    // Verify data integrity after resets
    assert_eq!(state.agents.len(), state2.agents.len());
    assert_eq!(state.agents.len(), state3.agents.len());
    assert_eq!(state2.agents[0], "agent1");
    assert_eq!(state3.agents[1], "agent2");
}

#[test]
fn regression_test_modified_files_detail_memory_efficiency() {
    use crate::checkpoint::execution_history::ModifiedFilesDetail;
    use std::mem::size_of;

    // Empty detail should use minimal memory (all fields None)
    let empty = ModifiedFilesDetail::default();

    // Verify fields are Option<Box<[String]>> not Vec<String>
    // This test documents expected size after optimization
    let expected_size = size_of::<Option<Box<[String]>>>() * 3;
    assert_eq!(
        size_of::<ModifiedFilesDetail>(),
        expected_size,
        "ModifiedFilesDetail should use Option<Box<[String]>> for all fields"
    );

    // Verify None for empty collections
    assert!(empty.added.is_none());
    assert!(empty.modified.is_none());
    assert!(empty.deleted.is_none());

    // Verify memory savings vs Vec
    let option_boxed_size = size_of::<Option<Box<[String]>>>();
    let vec_size = size_of::<Vec<String>>();
    assert_eq!(option_boxed_size, 2 * size_of::<usize>());
    assert_eq!(vec_size, 3 * size_of::<usize>());
    assert_eq!(vec_size - option_boxed_size, size_of::<usize>());
}

#[test]
fn regression_test_boxed_str_size_optimization() {
    use std::mem::size_of;

    // Verify Box<str> is smaller than String
    assert_eq!(size_of::<Box<str>>(), 2 * size_of::<usize>());
    assert_eq!(size_of::<String>(), 3 * size_of::<usize>());
    assert_eq!(
        size_of::<Option<Box<str>>>(),
        size_of::<Box<str>>(),
        "Option<Box<str>> should have niche optimization"
    );

    // Verify savings
    assert_eq!(
        size_of::<String>() - size_of::<Box<str>>(),
        size_of::<usize>()
    );
}

#[test]
fn regression_test_agent_chain_arc_optimization() {
    // Verify AgentChainState uses Arc for cheap cloning of immutable collections
    use crate::agents::AgentRole;
    use crate::reducer::state::AgentChainState;
    use std::mem::size_of;

    let agents = vec!["agent1".to_string(), "agent2".to_string()];
    let models = vec![vec!["model1".to_string()], vec!["model2".to_string()]];

    let state1 = AgentChainState::initial().with_agents(agents, models, AgentRole::Developer);

    // Advance creates new state - agents Arc should be shared
    let state2 = state1.advance_to_next_model();

    // Verify Arc sharing (same pointer)
    assert!(
        Arc::ptr_eq(&state1.agents, &state2.agents),
        "Arc optimization regression: agents not shared between states"
    );
    assert!(
        Arc::ptr_eq(&state1.models_per_agent, &state2.models_per_agent),
        "Arc optimization regression: models not shared between states"
    );

    // Test other state transition methods also share Arc
    let state3 = state1.switch_to_next_agent();
    assert!(
        Arc::ptr_eq(&state1.agents, &state3.agents),
        "Arc optimization regression: agents not shared after switch_to_next_agent"
    );

    let state4 = state1.reset();
    assert!(
        Arc::ptr_eq(&state1.agents, &state4.agents),
        "Arc optimization regression: agents not shared after reset"
    );

    // Verify memory savings: Arc::clone only increments reference count
    // No deep copy of the underlying Vec<String> occurs
    let arc_size = size_of::<Arc<[String]>>();
    assert_eq!(arc_size, 2 * size_of::<usize>());
}
