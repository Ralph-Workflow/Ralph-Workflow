# Memory Budget and Resource Management

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document describes the memory budgets, limits, and expected resource usage patterns for ralph-workflow. It provides guidance for production monitoring and helps prevent unbounded memory growth.

## Overview

Ralph-workflow implements bounded memory growth to ensure long-running pipelines do not exhibit unbounded memory growth. This is critical for production deployments where pipelines may run for hours or days with hundreds of iterations.

## Memory Budget Baseline

Based on benchmark measurements from the memory safety test suite:

### Core Pipeline State

| Component | Expected Size | Notes |
|-----------|--------------|-------|
| Baseline pipeline state | ~100 KB | Without execution history |
| Execution history (1000 entries, heap) | ~51 KB | Default limit in memory (measured ~53 KB) |
| Checkpoint (serialized, typical) | ~300-400 KB | JSON with bounded history; file snapshots can increase size |
| Parser buffers | <1 MB | Event queue and streaming buffers |
| **Total per pipeline (typical)** | **<5 MB** | Depends on file snapshots and log buffering |

### Execution History Growth

The execution history is the primary source of unbounded growth. Without bounding:
- **Before bounding**: Linear growth (1 entry per agent invocation)
- **After bounding**: Bounded at configurable limit (default: 1000 entries)

**Growth characteristics**:
- Each execution step: ~53 bytes heap (in-memory), ~384 bytes JSON serialized
- 1000 entries: ~51 KB heap, ~375 KB serialized
- Unbounded (pre-bounding): grows linearly with iterations

## Configured Limits

### Execution History Limit

**Configuration field**: `execution_history_limit`  
**Default value**: `1000` entries  
**Location**: `ralph-workflow/src/config/unified/types.rs`

The execution history maintains a bounded ring buffer that drops the oldest entries when the limit is reached. This ensures:
- Recent execution context is preserved for debugging
- Memory usage remains predictable
- Checkpoint files stay reasonable in size

**Rationale for default (1000)**:
- Provides sufficient context for debugging (last ~100-200 iterations)
- Keeps memory usage under 1 MB for execution history alone
- Results in checkpoint files <5 MB in typical scenarios

**Configuration override**:

```toml
# ~/.config/ralph-workflow.toml
# (Optional per-repo override: .agent/ralph-workflow.toml)
execution_history_limit = 2000  # Increase for deeper history
```

**When to adjust**:
- **Increase** if you need deeper execution history for debugging complex failures
- **Decrease** if running on memory-constrained environments
- **Monitor** checkpoint file sizes - if they grow >10 MB, consider reducing

### Event Queue Capacity

**Implementation**: `BoundedEventQueue`  
**Capacity**: `1000` events  
**Location**: `ralph-workflow/src/json_parser/event_queue/bounded_queue.rs`

The event queue implements backpressure to prevent unbounded buffering during JSON parsing.

### File Snapshot Thresholds

**Content threshold**: `10 KB` per file  
**Compression limit**: `100 KB` per compressed file  
**Purpose**: Prevents large files from bloating checkpoints

File snapshots larger than 10 KB are not stored in checkpoints to keep checkpoint sizes reasonable.

## Memory Safety Mechanisms

### 1. Bounded Execution History

**Implementation**: `PipelineState::add_execution_step()`  
**File**: `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395`

```rust
pub fn add_execution_step(&mut self, step: ExecutionStep, limit: usize) {
    self.execution_history.push_bounded(step, limit);
}

// Note: The actual implementation delegates to BoundedExecutionHistory::push_bounded,
// which enforces the limit using ring buffer semantics (oldest entries dropped first).
```

**Behavior**:
- Ring buffer semantics (oldest entries dropped first)
- Limit enforced on every insertion
- Maintains chronological order (most recent N entries)

### 2. Bounded Event Queue

**Implementation**: `BoundedEventQueue::new()`  
**File**: `ralph-workflow/src/json_parser/event_queue/bounded_queue.rs`

Uses `std::sync::mpsc::sync_channel` (bounded) instead of `channel` (unbounded) to apply backpressure when the queue fills.

### 3. Thread Lifecycle Management

All background threads are properly joined on shutdown to prevent resource leaks:
- File protection monitor thread: `src/files/protection/monitoring.rs`
- Streaming pump thread: `src/pipeline/prompt/streaming.rs`

**Exception**: Background monitor thread is not joined on panic (documented tradeoff for deadlock prevention).

### 4. Arc Reference Management

**Total Arc usages**: 880+ across codebase  
**Risk mitigation**: No circular references detected (verified by arc_patterns tests)

Common Arc patterns:
- Shared executors: `Arc<dyn ProcessExecutor>` (safe, no cycles)
- Shared printers: `Arc<dyn Printer>` (safe, no cycles)
- Cancellation tokens: `Arc<AtomicBool>` (safe, primitives)

## Production Monitoring

To monitor memory usage in production environments:

### 1. Track Checkpoint File Sizes

Monitor checkpoint files in `.ralph/checkpoints/`:

```bash
# Alert if checkpoint files exceed 10 MB
find .ralph/checkpoints -name "*.json" -size +10M
```

Expected sizes:
- **Normal**: 1-5 MB
- **Warning**: 5-10 MB (consider reducing execution_history_limit)
- **Critical**: >10 MB (indicates unbounded growth or config issue)

### 2. Monitor Execution History Length

Add instrumentation to log execution history length periodically:

```rust
if state.execution_history.len() > limit * 0.9 {
    warn!(
        "Execution history approaching limit: {}/{}",
        state.execution_history.len(),
        limit
    );
}
```

### 3. Memory Usage Alerts

Set process memory alerts:
- **Warning**: >500 MB resident memory
- **Critical**: >1 GB resident memory

Typical pipeline should stay under 100-200 MB for normal workloads.

### 4. Thread Leak Detection

Monitor thread count during pipeline execution:

```bash
# On Linux
ps -eLf | grep ralph-workflow | wc -l
```

Expected: Baseline + 1-2 background threads during execution, returning to baseline after completion.

## Benchmark Baselines

Established from memory safety benchmark tests (`ralph-workflow/src/benchmarks/`):

### Execution History Growth (per iteration)

| Metric | Value |
|--------|-------|
| Growth per entry (unbounded, heap estimate) | ~53 bytes |
| Growth per entry (bounded at 1000) | 0 bytes (after limit) |
| Memory for 1000 entries (heap estimate) | ~51 KB |
| Memory for 10,000 entries (unbounded, heap estimate) | ~530 KB |

### Checkpoint Serialization Performance

| State Size | Serialization Time | Checkpoint Size |
|------------|-------------------|-----------------|
| Small (10 steps) | <1 ms | ~8 KB |
| Medium (100 steps) | <5 ms | ~41 KB |
| Large (1000 steps) | <25 ms | ~375 KB |

**Note**: These are baseline measurements, not performance requirements. Actual times vary by hardware.

## Troubleshooting

### Checkpoint files growing unbounded

**Symptoms**: Checkpoint files >10 MB, increasing over time  
**Diagnosis**: Check `execution_history.len()` in checkpoint JSON  
**Resolution**:
1. Verify `execution_history_limit` is configured correctly
2. Check that `add_execution_step()` is being used (not raw `push()`)
3. Reduce `execution_history_limit` in config

### Memory usage increasing over time

**Symptoms**: Process RSS growing linearly with iterations  
**Diagnosis**: Profile with Valgrind or memory profiler  
**Resolution**:
1. Verify execution history is bounded
2. Check for Vec growth in other state fields
3. Review Arc usage for circular references
4. Check for file descriptor leaks

### Tests failing with OOM

**Symptoms**: Tests crash with out-of-memory errors  
**Diagnosis**: Likely unbounded growth in test scenario  
**Resolution**:
1. Ensure tests use `add_execution_step()` with limit
2. Reduce iteration count in tests
3. Add `#[ignore]` and run with `--release` for large tests

## Related Documentation

- [Event Loop and Reducers](./event-loop-and-reducers.md) - Pipeline state management
- [Effect System](./effect-system.md) - Side effect handling
- [Memory Safety Tests](../../tests/integration_tests/memory_safety/README.md) - Test suite details
- [Integration Tests Guide](../../tests/INTEGRATION_TESTS.md) - Testing philosophy

## Verification

Memory safety is verified through:
1. **Unit tests**: Benchmark tests in `ralph-workflow/src/benchmarks/`
2. **Integration tests**: Memory safety tests in `tests/integration_tests/memory_safety/`
3. **CI verification**: `cargo xtask verify`

Run verification:

```bash
# Full memory safety verification
cargo xtask verify

# Individual test suites
cargo xtask test -p ralph-workflow-tests memory_safety
cargo xtask test -p ralph-workflow benchmarks -- --nocapture
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable).
