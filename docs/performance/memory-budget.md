# Memory Budget and Performance Baselines (Historical - Rust Era)

> **This file describes the retired Rust implementation and is kept for archival reference only.**
> The Python package does not share the same memory management, commands, or code paths.
> For Python performance guidance, refer to the package docstrings and Sphinx documentation.

This document establishes memory budget expectations and performance baselines for the Ralph workflow pipeline based on comprehensive benchmarking and testing.

## Executive Summary

The Ralph pipeline implements bounded memory growth through a configurable execution history limit (default: 1000 entries). All memory safety tests pass, demonstrating:

- **Bounded growth**: Execution history does not grow unbounded
- **No memory leaks**: Arc patterns verified, no circular references
- **Thread safety**: All background threads properly cleaned up
- **Channel backpressure**: Event processing uses bounded channels

## Execution History Bounded Growth

**Historical Implementation:** `src/reducer/state/pipeline/core_state.rs:395-403` (Rust)

The `add_execution_step` method enforces bounded growth with configurable limits:

```rust
pub fn add_execution_step(&mut self, step: ExecutionStep, limit: usize) {
    self.execution_history.push_bounded(step, limit);
}

// Implementation uses BoundedExecutionHistory wrapper that enforces
// the limit via ring buffer semantics (drops oldest when limit exceeded).
```

### Default Configuration

- **Default limit:** 1000 entries
- **Memory per entry:** ~53 bytes (heap allocation)
- **Total heap at limit:** ~51 KB for execution history
- **Checkpoint size at limit:** ~375 KB (JSON serialized)

### Growth Characteristics

| Iterations | History Entries | Heap Size (Estimated) | Behavior |
|------------|----------------|-----------------------|----------|
| 10         | 10             | ~530 bytes            | Linear growth |
| 100        | 100            | ~5.3 KB               | Linear growth |
| 1000       | 1000           | ~51 KB                | At limit |
| 10000      | 1000           | ~51 KB                | Bounded (oldest dropped) |

**Source:** Benchmark tests in `src/benchmarks/memory_usage.rs`

### Sliding Window Semantics

When the limit is reached, oldest entries are dropped using FIFO (First-In-First-Out):

- Entries 0-499 are dropped when adding entries 1000-1499 (with default limit 1000)
- Maintains most recent context for debugging and analysis
- Prevents unbounded memory growth in long-running pipelines
- Entries remain contiguous (no gaps in iteration numbers)

**Verification:** Integration tests in `tests/integration_tests/memory_safety/bounded_growth.rs`

## Checkpoint Serialization Performance

### Serialization Time by History Size

Based on benchmark measurements with actual execution history data:

| History Size | Serialize Time | Deserialize Time | Checkpoint Size |
|-------------|----------------|------------------|-----------------|
| 0 entries   | ~102 µs        | ~102 µs          | ~4 KB           |
| 10 entries  | ~170 µs        | ~163 µs          | ~7 KB           |
| 100 entries | ~900 µs        | ~720 µs          | ~41 KB          |
| 1000 entries| ~7.7 ms        | ~10 ms           | ~375 KB         |

**Source:** Benchmark tests in `src/benchmarks/checkpoint_serialization.rs`

### Serialization Scaling

Serialization time scales linearly with history size:

- **10 entries**: 171 µs → 7 KB
- **50 entries**: 476 µs → 22 KB
- **100 entries**: 833 µs → 41 KB
- **500 entries**: 3.9 ms → 189 KB
- **1000 entries**: 7.7 ms → 375 KB

### Round-Trip Performance

Serialize + deserialize (100 entries):
- Serialize: ~890 µs
- Deserialize: ~720 µs
- **Total**: ~1.6 ms

### Performance Characteristics

- Serialization time scales **linearly** with history size (no performance cliffs)
- Bounded history (1000 entries) keeps checkpoint size < 500 KB
- Round-trip (serialize + deserialize) completes in < 100ms for bounded history
- Memory per entry in JSON: ~384 bytes (vs ~53 bytes in memory)

## Thread Lifecycle Management

All background threads are properly cleaned up under both normal and panic conditions.

### Thread Usage Patterns

#### 1. File Monitor Thread

**Location:** `src/files/protection/monitoring.rs:357-366`

**Purpose:** Monitor PROMPT.md for unauthorized modifications

**Cleanup Strategy:**
- Stop signal sent on Drop
- Thread handle stored for cleanup
- **Documented tradeoff:** Not joined in Drop (might be panicking)

**Verification:** Integration test `test_background_monitor_thread_does_not_prevent_shutdown`

#### 2. Streaming Pump Threads

**Location:** `src/pipeline/prompt/streaming.rs:176-183`

**Purpose:** Pump stdout from agent process in real-time

**Cleanup Strategy:**
- 2-second deadline for graceful shutdown
- Detached if deadline exceeded (best-effort cleanup)
- Short-lived (bounded by agent process lifetime)

**Verification:** Integration test `test_streaming_threads_cleaned_up_after_agent_invocation`

### Thread Safety Verification

Integration tests verify thread lifecycle correctness:

1. **No hangs**: Pipeline completes without blocking (timeout wrapper detects hangs)
2. **No leaks**: 20 rapid start/stop cycles show no thread accumulation
3. **Clean shutdown**: All threads properly joined or documented as detached
4. **Panic resilience**: Error paths don't leave threads hanging

**Test suite:** `tests/integration_tests/memory_safety/thread_lifecycle.rs` (8 tests, all passing)

## Arc Reference Patterns

All Arc usage follows correct patterns with **no circular references**.

### Arc Usage Statistics

- **Total Arc matches**: 243 instances across 71 files
- **Nested Arc patterns**: 2 (both for `Arc<AtomicBool>` in monitor threads)
- **Weak references**: 0 (no intentional circular reference patterns)

### Common Arc Patterns

#### 1. Executor Arc (Process Executor)

```rust
let executor: Arc<dyn ProcessExecutor> = Arc::new(MockProcessExecutor::new());
```

**Usage:** Shared across pipeline phases for process spawning

**Cleanup:** Strong_count returns to baseline after pipeline completion

**Verification:** `test_executor_arc_cleanup_after_pipeline` (arc_patterns.rs:72)

#### 2. Workspace Arc (File System Operations)

```rust
let workspace: Arc<dyn Workspace> = Arc::new(MemoryWorkspace::new_test());
```

**Usage:** Shared workspace for file operations across pipeline

**Cleanup:** Strong_count stable, no accumulation across operations

**Verification:** `test_workspace_arc_cleanup_after_multiple_operations` (arc_patterns.rs:151)

#### 3. Monitor Thread Arc (Atomic Flags)

```rust
let stop_signal = Arc::new(AtomicBool::new(false));
```

**Usage:** Shared stop signal between main thread and monitor thread

**Cleanup:** Dropped when monitor thread exits

**Verification:** Thread lifecycle tests verify no hangs

### Arc Safety Guarantees

- **No circular references**: No `Weak<T>` usage found in codebase
- **Proper cleanup**: Strong_count returns to baseline after use
- **No accumulation**: Multiple pipeline runs don't leak Arc references
- **Nested safety**: Nested Arc usage (Arc in Arc) verified safe

**Test suite:** `tests/integration_tests/memory_safety/arc_patterns.rs` (8 tests, all passing)

## Channel Backpressure

All channels use bounded capacity where appropriate, with documented exceptions for system channels.

### Bounded Channel Pattern (Recommended)

```rust
use std::sync::mpsc;

// CORRECT: Bounded channel with capacity limit
let (tx, rx) = mpsc::sync_channel::<Event>(100);

// Backpressure: try_send returns TrySendError::Full when at capacity
match tx.try_send(event) {
    Ok(()) => { /* sent successfully */ }
    Err(mpsc::TrySendError::Full(_)) => { /* channel full, apply backpressure */ }
    Err(mpsc::TrySendError::Disconnected(_)) => { /* receiver dropped */ }
}
```

### Primary Bounded Channel Implementation

**Location:** `src/json_parser/event_queue/bounded_queue.rs:84`

```rust
let (sender, receiver) = mpsc::sync_channel(config.capacity);
```

**Usage:** JSON event processing queue (critical path)

**Capacity:** Configurable via `BoundedQueueConfig`

**Verification:** `test_sync_channel_applies_backpressure` (channel_bounds.rs:21)

### Documented Channel Exceptions

#### 1. File System Monitoring Callback

**Location:** `src/files/protection/monitoring.rs`

**Usage:** File system watcher callback must not block

```rust
let (tx, rx) = std::sync::mpsc::sync_channel(NOTIFY_EVENT_QUEUE_CAPACITY);
let mut watcher = notify::recommended_watcher(move |res| {
    // Best-effort: drop on full rather than blocking the callback.
    let _ = tx.try_send(res);
})?;
```

**Justification:** Callback must not block; bounded queue caps memory and drops bursts

#### 2. Short-Lived Stdout Pumping

**Location:** `src/pipeline/prompt/streaming.rs`

**Usage:** Stdout from agent process (real-time streaming)

```rust
let (tx, rx) = mpsc::sync_channel(STDOUT_PUMP_CHANNEL_CAPACITY);
let pump_handle = spawn_stdout_pump(stdout, activity_timestamp, tx, cancel);
```

**Justification:** Bounded buffering caps memory; backpressure applies when parsing falls behind pumping

### Channel Safety Verification

Integration tests verify bounded channel behavior:

1. **Backpressure applied**: `try_send` fails with `TrySendError::Full` when at capacity
2. **Proper draining**: Channels drain completely on shutdown
3. **High throughput**: 100-event test verifies producer/consumer pattern
4. **Capacity limits**: Tests verify limits from 10 to 1000 capacity

**Test suite:** `tests/integration_tests/memory_safety/channel_bounds.rs` (6 tests, all passing)

## Unsafe Code Verification

The codebase contains 4 unsafe blocks, all in `src/executor/real.rs` for Unix system calls.

### Unsafe Block Inventory

| Line Range | Operation | Purpose | Safety Justification |
|-----------|-----------|---------|---------------------|
| 16-24 | fcntl (F_GETFL, F_SETFL) | Set non-blocking I/O on file descriptors | Called with valid fd owned by this process |
| 40-42 | kill (SIGTERM) | Send SIGTERM to process group | Called with valid PID from child process |
| 53-55 | kill (SIGKILL) | Send SIGKILL to process group (forced) | Called with valid PID from child process |
| 164-171 | setpgid | Create new process group for agent | Called in pre_exec before child runs |

### Safety Verification Through Behavioral Tests

Rather than testing unsafe code directly, tests verify **observable behavior**:

#### 1. Non-blocking I/O (fcntl)

**Test:** `test_nonblocking_io_setup_succeeds` (`ralph-workflow/src/executor/mod.rs` in `tests::safety`)

**Verifies:** File descriptors are properly configured for non-blocking reads

#### 2. Process Termination (kill SIGTERM/SIGKILL)

**Test:** `test_process_termination_cleanup_works` (`ralph-workflow/src/executor/mod.rs` in `tests::safety`)

**Verifies:** Processes are properly terminated when requested

#### 3. Process Group Isolation (setpgid)

**Test:** `test_process_group_creation_succeeds` (`ralph-workflow/src/executor/mod.rs` in `tests::safety`)

**Verifies:** Agent processes run in separate process groups

### Safety Test Results

All 6 unsafe code safety tests pass:

```bash
cargo xtask test -p ralph-workflow --lib executor::tests::safety --quiet
test result: ok. 6 passed; 0 failed; 0 ignored; 0 measured
```

## Production Recommendations

### Memory Limits

Based on benchmark and test results, recommended configurations:

#### Default Configuration (Most Workflows)

- **Execution history limit:** 1000 entries
- **Expected memory usage:** ~51 KB (history) + ~375 KB (checkpoint)
- **Suitable for:** Typical workflows with < 10,000 iterations

#### High-Context Configuration (Debugging)

- **Execution history limit:** 2000 entries
- **Expected memory usage:** ~102 KB (history) + ~750 KB (checkpoint)
- **Suitable for:** Workflows requiring extensive debugging context

#### Low-Memory Configuration (Resource-Constrained)

- **Execution history limit:** 500 entries
- **Expected memory usage:** ~26 KB (history) + ~190 KB (checkpoint)
- **Suitable for:** Embedded systems or high-concurrency deployments

### Monitoring Metrics

Monitor these metrics in production to detect regressions:

#### 1. Execution History Growth

**Metric:** `execution_history.len()`

**Expected:** Should **plateau** at configured limit (e.g., 1000)

**Red flag:** Linear growth beyond limit indicates bounding mechanism failure

#### 2. Checkpoint File Size

**Metric:** Checkpoint file size on disk

**Expected:** Should stay < 500 KB with default limit (1000 entries)

**Red flag:** Checkpoint size > 1 MB indicates unbounded growth or serialization issue

#### 3. Memory Usage Over Time

**Metric:** Process RSS (Resident Set Size)

**Expected:** Bounded growth, should stabilize after initial pipeline setup

**Red flag:** Continuous linear growth indicates memory leak

#### 4. Serialization Performance

**Metric:** Checkpoint serialize/deserialize time

**Expected:** < 100ms round-trip for bounded history (1000 entries)

**Red flag:** Serialization time > 1 second indicates performance regression

### Regression Detection

To detect performance regressions, run benchmarks before/after changes:

```bash
# Capture baseline
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture > baseline.txt

# After changes, compare
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture > current.txt
diff baseline.txt current.txt
```

#### Red Flags

1. **Execution history grows unbounded** (> configured limit)
2. **Checkpoint size exceeds 1 MB** with default limit
3. **Serialization time exceeds 100ms** for bounded history
4. **Memory growth doesn't plateau** over iterations
5. **Arc strong_count doesn't return to baseline** after operations
6. **Thread count increases** across multiple pipeline runs

## Verification Commands

All memory safety tests can be verified with:

```bash
# Full memory safety verification suite
cargo xtask verify

# Individual test suites
cargo xtask test -p ralph-workflow-tests --test integration_tests memory_safety::bounded_growth
cargo xtask test -p ralph-workflow-tests --test integration_tests memory_safety::thread_lifecycle
cargo xtask test -p ralph-workflow-tests --test integration_tests memory_safety::arc_patterns
cargo xtask test -p ralph-workflow-tests --test integration_tests memory_safety::channel_bounds

# Benchmark tests (informational)
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture

# Unsafe code behavioral verification
cargo xtask test -p ralph-workflow --lib executor::tests::safety
```

**Expected result:** tests pass with **no ERROR/WARNING diagnostics** (informational output is acceptable)

## Performance Baseline Summary

### Memory Growth

- **10 iterations**: 530 bytes heap
- **100 iterations**: 5.3 KB heap
- **1000 iterations**: 51 KB heap (at limit)
- **10,000 iterations**: 51 KB heap (bounded)

**Growth rate**: ~53 bytes per entry until limit, then plateaus

### Checkpoint Performance

- **Small state (10 entries)**: 170 µs serialize, 7 KB size
- **Medium state (100 entries)**: 900 µs serialize, 41 KB size
- **Large state (1000 entries)**: 7.7 ms serialize, 375 KB size

**Scaling**: Linear with no performance cliffs

### Thread Lifecycle

- **Single run**: Completes without hanging
- **10 sequential runs**: No thread accumulation
- **20 rapid runs**: No thread leaks detected

**Cleanup**: All threads properly joined or documented as detached

### Arc Reference Counting

- **Pattern**: Strong_count increases during use, returns to baseline after
- **Multiple operations**: No accumulation across pipeline runs
- **Nested usage**: Correctly handled without leaks

**Safety**: No circular references detected

### Channel Backpressure

- **Bounded channels**: Apply backpressure when full (TrySendError::Full)
- **High throughput**: 100 events processed correctly with 50-capacity channel
- **Draining**: Channels drain completely on shutdown

**Pattern**: `sync_channel` (bounded) used for critical paths

## Acceptance Criteria (All Met)

✅ Benchmark test suite exists and runs in CI
✅ Memory growth tests demonstrate bounded behavior
✅ Thread cleanup tests pass under normal and panic conditions
✅ No memory leaks detected in long-running test scenarios (10,000 iterations)
✅ All existing tests continue to pass (2984 unit + 828 integration + 130 doc tests)
✅ Performance baselines documented for future comparison
✅ Investigation findings documented in test comments and this document

## References

### Test Files

- **Benchmark tests**: `ralph-workflow/src/benchmarks/`
- **Integration tests**: `tests/integration_tests/memory_safety/`
- **Unsafe code tests**: `ralph-workflow/src/executor/mod.rs` (`tests::safety` module)

### Implementation Files

- **Bounded growth**: `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395-403`
- **Checkpoint serialization**: `ralph-workflow/src/checkpoint/execution_history.rs`
- **Thread lifecycle**: `ralph-workflow/src/files/protection/monitoring.rs`, `ralph-workflow/src/pipeline/prompt/streaming.rs`
- **Bounded channels**: `ralph-workflow/src/json_parser/event_queue/bounded_queue.rs`
- **Unsafe code**: `ralph-workflow/src/executor/real.rs`

### Verification Scripts

- **Full verification**: `cargo xtask verify` (see `docs/agents/verification.md`)
