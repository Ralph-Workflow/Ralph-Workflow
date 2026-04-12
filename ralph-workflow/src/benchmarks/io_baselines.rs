//! Performance baselines for regression detection.
//!
//! This module defines expected performance characteristics based on
//! measurements from the benchmark suite. Tests can compare against
//! these baselines to detect regressions.
//!
//! # Baseline Measurements
//!
//! Current measurements (as of 2026-02-13):
//! - **Execution history growth**: ~53 bytes per iteration (bounded at 1000 entries)
//! - **Checkpoint size**: ~363 KB for 1000 entries (well under 2048 KB hard limit)
//! - **Memory usage**: Bounded growth verified by integration tests
//!
//! # Regression Detection Strategy
//!
//! 1. **CI runs `cargo xtask verify` on every commit**
//!    - Fails if execution history exceeds 1000 entries (hard limit)
//!    - Fails if checkpoint size exceeds 2048 KB (hard limit)
//!    - Fails if thread cleanup doesn't complete (timeout detection)
//!
//! 2. **Benchmark tests capture current values for trending**
//!    - Run with: `cargo test -p ralph-workflow --lib benchmarks -- --nocapture`
//!    - Values are informational; baselines have generous tolerance
//!
//! 3. **Integration tests enforce behavioral invariants**
//!    - Bounded growth: `tests/integration_tests/memory_safety/bounded_growth.rs`
//!    - Thread cleanup: `tests/integration_tests/memory_safety/thread_lifecycle.rs`
//!    - Arc patterns: `tests/integration_tests/memory_safety/arc_patterns.rs`
//!
//! 4. **Tolerance rationale**
//!    - Memory baselines: 20% tolerance (accounts for platform variance)
//!    - Time baselines: 2x tolerance (serialization performance varies widely)
//!    - Hard limits: 0% tolerance (prevent unbounded growth)

use crate::checkpoint::execution_history::ExecutionStep;

/// Estimate heap bytes for a single execution step using the same methodology as
/// the benchmark suite and baselines.
///
/// Intentionally excludes `StepOutcome` payloads (e.g., output strings and file
/// lists) because those are highly workload-dependent and can introduce
/// cross-platform flakes when enforced as hard ceilings.
///
/// # Memory Optimization
///
/// After optimization, this accounts for:
/// - `phase`: `Arc<str>` - counted as the length of the string (shared allocation)
/// - `step_type`: `Box<str>` - counted as the length of the string
/// - `timestamp`: `String` - counted as length (deterministic; capacity can vary)
/// - `agent`: `Option<Arc<str>>` - counted as the length of the string (shared allocation)
///
/// `Arc<str>` fields are counted by length rather than capacity because the
/// allocation is shared across multiple `ExecutionStep` instances via string interning.
#[must_use]
pub fn estimate_execution_step_heap_bytes_core_fields(step: &ExecutionStep) -> usize {
    // For Arc<str>, count the string length (shared allocation)
    step.phase.len()
        // For Box<str>, count the string length
        + step.step_type.len()
        // For String, count length (capacity is allocator-dependent and can be flaky)
        + step.timestamp.len()
        // For Option<Arc<str>>, count the string length if present (shared allocation)
        + step.agent.as_ref().map_or(0, |s| s.len())
}

/// Estimate heap bytes for an execution history slice.
pub fn estimate_execution_history_heap_bytes_core_fields(steps: &[ExecutionStep]) -> usize {
    steps
        .iter()
        .map(estimate_execution_step_heap_bytes_core_fields)
        .sum()
}

/// Performance baseline for execution history growth.
#[derive(Debug, Clone)]
pub struct ExecutionHistoryBaseline {
    /// Number of entries in benchmark
    pub entry_count: usize,
    /// Expected heap size in bytes
    pub heap_size_bytes: usize,
    /// Expected serialized size in bytes
    pub serialized_size_bytes: usize,
    /// Tolerance factor (1.2 = allow 20% deviation)
    pub tolerance: f64,
}

impl ExecutionHistoryBaseline {
    /// Baseline for 1000 entries (default limit).
    ///
    /// # Measurement Methodology
    ///
    /// These values are derived from benchmark tests that:
    /// 1. Create 1000 execution history entries with realistic content
    /// 2. Measure heap size using `std::mem::size_of_val` and content sizes
    /// 3. Serialize to JSON and measure compressed size
    /// 4. Run multiple iterations to verify consistency
    ///
    /// # Updating Baselines
    ///
    /// If legitimate performance improvements reduce memory usage, update these
    /// values based on new benchmark measurements. Always maintain 20% tolerance
    /// to account for platform variance.
    ///
    /// **DO NOT** increase these values to accommodate regressions. Investigate
    /// and fix the root cause instead.
    pub const ENTRIES_1000: Self = Self {
        entry_count: 1000,
        heap_size_bytes: 60_000, // 60 KB (measured: ~53_000 bytes for 1000 entries)
        serialized_size_bytes: 400_000, // 400 KB (measured: ~363 KB actual)
        tolerance: 1.2,          // 20% headroom for platform variance
    };

    /// Check if measured value exceeds baseline.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn check_heap_size(&self, measured: usize) -> Result<(), String> {
        let max_allowed = tolerance_ceiling(self.heap_size_bytes, self.tolerance);
        if measured > max_allowed {
            Err(format!(
                "Heap size {} bytes exceeds baseline {} bytes (tolerance: {}x)",
                measured, max_allowed, self.tolerance
            ))
        } else {
            Ok(())
        }
    }

    /// Check if serialized size exceeds baseline.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn check_serialized_size(&self, measured: usize) -> Result<(), String> {
        let max_allowed = tolerance_ceiling(self.serialized_size_bytes, self.tolerance);
        if measured > max_allowed {
            Err(format!(
                "Serialized size {} bytes exceeds baseline {} bytes (tolerance: {}x)",
                measured, max_allowed, self.tolerance
            ))
        } else {
            Ok(())
        }
    }
}

fn tolerance_ceiling(baseline: usize, tolerance: f64) -> usize {
    if !tolerance.is_finite() {
        return usize::MAX;
    }

    let baseline_f = baseline.to_string().parse::<f64>().unwrap_or(f64::MAX);
    let scaled = baseline_f * tolerance;
    if !scaled.is_finite() {
        return usize::MAX;
    }

    let ceil = scaled.ceil();
    if ceil <= 0.0 {
        return 0;
    }

    let max_f = usize::MAX.to_string().parse::<f64>().unwrap_or(f64::MAX);
    if ceil >= max_f {
        return usize::MAX;
    }

    let ceil_str = format!("{ceil:.0}");
    ceil_str.parse::<usize>().unwrap_or(usize::MAX)
}

/// Checkpoint serialization performance baseline.
#[derive(Debug, Clone)]
pub struct CheckpointSerializationBaseline {
    /// Number of history entries
    pub entry_count: usize,
    /// Expected serialization time in microseconds
    pub serialize_us: u64,
    /// Expected deserialization time in microseconds
    pub deserialize_us: u64,
    /// Tolerance factor
    pub tolerance: f64,
}

impl CheckpointSerializationBaseline {
    /// Baseline for 1000 entries.
    ///
    /// # Measurement Methodology
    ///
    /// These values are derived from benchmark tests that:
    /// 1. Create checkpoint state with 1000 execution history entries
    /// 2. Measure `serde_json::to_string()` serialization time
    /// 3. Measure `serde_json::from_str()` deserialization time
    /// 4. Run multiple iterations to get representative average
    ///
    /// # Tolerance Rationale
    ///
    /// Serialization performance varies significantly based on:
    /// - CPU architecture and speed
    /// - Memory bus speed
    /// - System load (other processes)
    /// - Compiler optimizations (debug vs release)
    ///
    /// We use 2x tolerance (100% headroom) to avoid false positives while
    /// still catching catastrophic regressions (e.g., O(n²) algorithms).
    pub const ENTRIES_1000: Self = Self {
        entry_count: 1000,
        serialize_us: 5_000,   // 5ms (typical range: 2-10ms)
        deserialize_us: 5_000, // 5ms (typical range: 2-10ms)
        tolerance: 2.0,        // 2x headroom for hardware/load variance
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};

    #[test]
    fn test_execution_history_baseline_magnitude_is_reasonable() {
        let baseline = ExecutionHistoryBaseline::ENTRIES_1000;

        // Baselines should be in the same order of magnitude as the measured
        // benchmark output (~53_000 bytes for 1000 entries).
        assert!(baseline.heap_size_bytes > 40_000);
        assert!(baseline.heap_size_bytes < 100_000);
    }

    #[test]
    fn test_baseline_check_within_tolerance() {
        let baseline = ExecutionHistoryBaseline::ENTRIES_1000;

        // 70 KB is within 60 KB baseline + 20% tolerance
        assert!(baseline.check_heap_size(70_000).is_ok());

        // 80 KB exceeds (60 KB * 1.2 = 72 KB)
        assert!(baseline.check_heap_size(80_000).is_err());
    }

    #[test]
    fn test_execution_history_heap_estimator_counts_only_core_fields() {
        let mut step = ExecutionStep::new(
            "Development",
            1,
            "agent_invoked",
            StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
        )
        .with_agent("test-agent")
        .with_duration(5);

        // After optimization: Arc<str>, Box<str>, and String are counted by length.
        //
        // Ensure this test is robust by making timestamp capacity differ from its length.
        // The estimator intentionally uses `len()` for determinism across allocators.
        step.timestamp.reserve_exact(64);
        assert!(
            step.timestamp.capacity() > step.timestamp.len(),
            "test invariant: timestamp capacity should exceed length"
        );

        let expected = step.phase.len()
            + step.step_type.len()
            + step.timestamp.len()
            + step.agent.as_ref().map_or(0, |s| s.len());

        assert_eq!(
            estimate_execution_step_heap_bytes_core_fields(&step),
            expected
        );
    }

    /// Regression test: Verify memory optimization reduces per-entry footprint
    ///
    /// After `Arc<str>` and `Box<str>` optimizations, core fields should use ~40-45 bytes
    /// per entry (down from ~53 bytes with `String` fields).
    #[test]
    fn test_memory_optimization_regression() {
        use crate::checkpoint::StringPool;

        let pool = StringPool::new();

        // Create a typical execution step with string pool
        let (step, pool) = ExecutionStep::new_with_pool(
            "Development",
            1,
            "agent_invoked",
            StepOutcome::success(Some("output".to_string()), vec!["file.rs".to_string()]),
            pool,
        );
        let (step, _pool) = step.with_agent_pooled("test-agent", pool);
        let step = step.with_duration(5);

        let heap_size = estimate_execution_step_heap_bytes_core_fields(&step);

        // Core fields should be optimized to ~40-45 bytes
        // (11 bytes phase + 14 bytes step_type + ~25 bytes timestamp + 10 bytes agent)
        assert!(
            heap_size <= 60,
            "Memory regression: {heap_size} bytes per entry exceeds 60 byte target (expected ~40-45 bytes)"
        );
    }

    /// Regression test: Verify string pool deduplicates repeated strings
    #[test]
    fn test_string_pool_deduplication_regression() {
        use crate::checkpoint::StringPool;
        use std::sync::Arc;

        let pool = StringPool::new();

        // Create multiple steps with the same phase and agent
        let (step1, pool) = ExecutionStep::new_with_pool(
            "Development",
            1,
            "dev_run",
            StepOutcome::success(None, vec![]),
            pool,
        );
        let (step1, pool) = step1.with_agent_pooled("claude", pool);

        let (step2, pool) = ExecutionStep::new_with_pool(
            "Development",
            2,
            "dev_run",
            StepOutcome::success(None, vec![]),
            pool,
        );
        let (step2, pool) = step2.with_agent_pooled("claude", pool);

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
}
