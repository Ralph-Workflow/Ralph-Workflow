# Performance Optimization Guide (Historical - Rust Era)

> **This file describes the retired Rust implementation and is kept for archival reference only.**
> The Python package does not share the same optimization patterns, commands, or code paths.
> For Python optimization guidance, refer to the package docstrings and Sphinx documentation.

This document describes the performance optimizations applied to the Ralph pipeline
to improve memory efficiency and reduce unnecessary allocations.

## Overview

The Ralph pipeline has been optimized to reduce memory usage and CPU overhead through:

1. **String interning** - Deduplicating repeated phase and agent names
2. **Copy trait for simple enums** - Eliminating unnecessary clones in hot paths
3. **Efficient serialization** - Pre-allocated buffers and compact JSON encoding
4. **Memory-efficient data structures** - Using Box<str> and Option<Box<[T]>>

## Optimizations

### 1. String Interning with Arc<str>

**Problem:** Phase names ("Development", "Review") and agent names are repeated
frequently across execution history entries, causing redundant memory allocations.

**Solution:** Use `Arc<str>` with a `StringPool` to share string allocations.

**Implementation:**
```rust
use crate::checkpoint::StringPool;

let mut pool = StringPool::new();
let step = ExecutionStep::new_with_pool(
    "Development",  // Interned via pool
    1,
    "dev_run",
    outcome,
    &mut pool,
).with_agent_pooled("claude", &mut pool);  // Interned via pool
```

**Benefits:**
- Multiple ExecutionStep instances share the same Arc<str> allocation
- Memory savings: ~40-50% reduction for phase and agent fields
- Example: 1000 entries with same phase saves ~11KB (11 bytes * 999 duplicate strings)

**Files:**
- `ralph-workflow/src/checkpoint/string_pool.rs` - StringPool implementation
- `ralph-workflow/src/checkpoint/execution_history.rs` - ExecutionStep with Arc<str>

### 2. Copy Trait for Simple Enums

**Problem:** Simple enums without heap-allocated fields were unnecessarily cloned
in hot paths, causing CPU overhead.

**Solution:** Add `Copy` trait to simple enums that don't contain heap-allocated data.

**Implementation:**
```rust
// Before: Clone only
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ArtifactType { ... }

// After: Copy + Clone
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ArtifactType { ... }
```

**Optimized Enums:**
- `ArtifactType` - Artifact being processed (Plan, DevelopmentResult, etc.)
- `PromptMode` - Prompt rendering mode (Normal, XsdRetry, Continuation)
- `SameAgentRetryReason` - Retry reason (Timeout, InternalError, Other)
- `DevelopmentStatus` - Development status (Completed, Partial, Failed)
- `FixStatus` - Fix status (AllIssuesAddressed, IssuesRemain, etc.)
- `PromptInputKind` - Input kind (Prompt, Plan, Diff, LastOutput)
- `PromptMaterializationReason` - Why input was materialized a certain way

**Benefits:**
- Eliminates unnecessary `.clone()` calls in hot paths
- CPU performance improvement (no heap operations for simple copies)
- Clippy now warns about unnecessary clones, preventing future regressions

**Example Fix:**
```rust
// Before (unnecessary clone)
let artifact = state.continuation.current_artifact.clone();

// After (efficient copy)
let artifact = state.continuation.current_artifact;
```

**Files:**
- `ralph-workflow/src/reducer/state/enums.rs` - Enum definitions

### 3. Memory-Efficient Data Structures

**Problem:** `Vec<T>` over-allocates capacity, and empty collections waste memory.

**Solution:** Use `Box<str>` for strings and `Option<Box<[T]>>` for collections.

**Implementation:**
```rust
pub enum StepOutcome {
    Success {
        output: Option<Box<str>>,                // Exact size, not over-allocated
        files_modified: Option<Box<[String]>>,   // None when empty, exact size otherwise
        exit_code: Option<i32>,
    },
    Failure {
        error: Box<str>,                         // Exact size
        recoverable: bool,
        exit_code: Option<i32>,
        signals: Option<Box<[String]>>,          // None when empty
    },
    // ...
}
```

**Benefits:**
- `Box<str>` uses exact string length (no capacity overhead)
- `Option<Box<[T]>>` uses `None` for empty collections (saves Vec header size)
- `Box<[T]>` uses exact slice size (no capacity field like Vec)

**Memory Comparison:**
```
String:           len (8) + capacity (8) + ptr (8) = 24 bytes + heap allocation
Box<str>:         len (8) + ptr (8) = 16 bytes + heap allocation (exact size)

Vec<String>:      len (8) + capacity (8) + ptr (8) = 24 bytes + heap allocations
Option<Box<[T]>>: None = 8 bytes, Some = 16 bytes + heap allocation (exact size)
```

**Files:**
- `ralph-workflow/src/checkpoint/execution_history.rs` - StepOutcome enum

### 4. Optimized Checkpoint Serialization

**Problem:** Checkpoint serialization took 8-10ms for 1000 entries due to
repeated reallocations during JSON serialization.

**Solution:** Pre-allocate buffer based on estimated checkpoint size, use
compact JSON encoding (no pretty-printing).

**Implementation:**
```rust
fn save_checkpoint_with_workspace(
    workspace: &dyn Workspace,
    checkpoint: &PipelineCheckpoint,
) -> io::Result<()> {
    // Estimate size: base (10KB) + entries * 400 bytes
    let estimated_size = estimate_checkpoint_size(checkpoint);
    let mut buf = Vec::with_capacity(estimated_size);

    // Use compact serialization (no pretty printing)
    serde_json::to_writer(&mut buf, checkpoint)?;

    // Convert the serialized bytes to UTF-8 with error handling.
    // Avoid `unsafe`: if bytes are not valid UTF-8, surface a structured error.
    let json = String::from_utf8(buf).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Checkpoint JSON was not valid UTF-8: {e}"),
        )
    })?;

    workspace.write_atomic(Path::new(&checkpoint_path()), &json)
}
```

**Benefits:**
- Pre-allocation eliminates reallocation overhead
- Compact JSON reduces output size by ~15-20% (no whitespace)
- Serialization time: ~5-6ms (down from 8-10ms)

**Files:**
- `ralph-workflow/src/checkpoint/state/serialization.rs` - Optimized serialization

## Performance Baselines

Current performance baselines after optimizations:

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Heap per entry | 53 bytes | <=60 bytes | OK (within target) |
| Serialization (1000 entries) | 8ms | <=10ms | OK (within target) |
| Checkpoint size (1000 entries) | 375 KB | <=400 KB | OK (within target) |
| Memory growth rate | 53 bytes/entry | Linear | OK (bounded) |

## Anti-Patterns to Avoid

### X Don't Clone Copy Types

```rust
// BAD: Unnecessary clone for Copy type
let status = state.continuation.previous_status.clone();

// GOOD: Direct copy
let status = state.continuation.previous_status;
```

### X Don't Use Vec for Small Fixed-Size Collections

```rust
// BAD: Over-allocation for fixed-size data
pub struct Outcome {
    files: Vec<String>,  // May allocate capacity for 4+ even if only 1 file
}

// GOOD: Exact allocation
pub struct Outcome {
    files: Option<Box<[String]>>,  // None when empty, exact size otherwise
}
```

### X Don't Repeat String Allocations

```rust
// BAD: Repeated allocations for same string
for i in 0..1000 {
    steps.push(ExecutionStep::new("Development", i, ...));  // 1000 "Development" allocations
}

// GOOD: Share allocation via Arc<str>
let mut pool = StringPool::new();
for i in 0..1000 {
    steps.push(ExecutionStep::new_with_pool("Development", i, ..., &mut pool));  // 1 allocation
}
```

## Testing Performance Changes

### Regression Tests

Run performance regression tests to verify optimizations:

```bash
# All regression tests
cargo xtask test --lib benchmarks::regression_tests

# Memory footprint
cargo xtask test --lib regression_test_execution_step_memory_footprint

# String pool sharing
cargo xtask test --lib regression_test_string_pool_sharing

# Serialization performance (with performance ceilings enabled)
RALPH_WORKFLOW_PERF_CEILINGS=1 cargo xtask test --lib regression_test_serialization_performance
```

### Benchmarks

Run benchmarks to measure current performance:

```bash
# All benchmarks (measurement only, not pass/fail)
cargo xtask test --lib benchmarks -- --nocapture

# Memory usage benchmarks
cargo xtask test --lib benchmarks::memory_usage -- --nocapture

# Serialization benchmarks
cargo xtask test --lib benchmarks::checkpoint_serialization -- --nocapture

# Baseline validation
cargo xtask test --lib benchmarks::baselines::tests
```

### Verification

Before committing performance changes, run full verification:

```bash
# Format and lint
cargo xtask fmt --all --check
cargo xtask clippy -p ralph-workflow --lib --all-features -- -D warnings

# Unit tests
cargo xtask test -p ralph-workflow --lib --all-features

# Integration tests (if available)
cargo xtask test -p ralph-workflow-tests

# Full verification suite (includes memory safety and performance regression)
cargo xtask verify
```

## Updating Baselines

When legitimate performance improvements reduce memory usage:

1. **Update baseline constants** in `ralph-workflow/src/benchmarks/baselines.rs`:
   ```rust
   pub const ENTRIES_1000: Self = Self {
       entry_count: 1000,
       heap_size_bytes: 50_000,     // Update based on new measurements
       serialized_size_bytes: 350_000,  // Update based on new measurements
       tolerance: 1.2,              // Keep 20% headroom
   };
   ```

2. **Update regression tests** in `ralph-workflow/src/benchmarks/regression_tests.rs`:
   ```rust
   #[test]
   fn regression_test_execution_step_memory_footprint() {
       let heap_size = estimate_execution_step_heap_bytes_core_fields(&step);
       
       // Update threshold if optimization improves memory usage
       assert!(heap_size <= 60, "Memory regression: {} bytes", heap_size);
   }
   ```

3. **Update documentation** in `docs/performance/README.md`:
   ```markdown
   | Component | Typical | Maximum |
   |-----------|---------|---------|
   | Execution History (heap) | ~45-55 KB | ~65 KB |  # Update based on new baselines
   ```

4. **Never increase baselines to accommodate regressions** - Always investigate
   and fix the root cause of performance degradation.

## Profiling Tools

For deeper performance analysis:

### CPU Profiling

```bash
# Using cargo-flamegraph (install: cargo install flamegraph)
cargo flamegraph --test integration_tests -- memory_safety::long_running_pipeline

# Using perf (Linux)
perf record --call-graph=dwarf cargo test --release
perf report
```

### Memory Profiling

```bash
# Using valgrind (install: apt-get install valgrind)
valgrind --tool=massif cargo test --release

# Using heaptrack (Linux, install: apt-get install heaptrack)
heaptrack cargo test --release
```

### Benchmark Comparison

```bash
# Before optimization
cargo xtask test --lib benchmarks -- --nocapture > before.txt

# After optimization
cargo xtask test --lib benchmarks -- --nocapture > after.txt

# Compare results
diff -u before.txt after.txt
```

## Contributing

When making performance-related changes:

1. **Profile first** - Measure current performance to establish baseline
2. **Optimize targeted** - Focus on hot paths identified by profiling
3. **Test thoroughly** - Run all regression tests and benchmarks
4. **Document changes** - Update this guide and inline comments
5. **Maintain baselines** - Update baselines if intentional improvements occur
6. **No regressions** - Never increase baselines to hide regressions

## See Also

- [Memory Budget](./memory-budget.md) - Expected memory usage patterns
- [Monitoring Guide](./monitoring-guide.md) - Production monitoring
- [AGENTS.md](../../AGENTS.md) - General contribution guidelines
- [Verification Guide](../agents/verification.md) - Required verification steps
