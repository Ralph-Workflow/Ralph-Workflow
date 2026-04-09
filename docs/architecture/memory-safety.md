# Memory Safety and Resource Management

This document describes Ralph's memory safety mechanisms, performance baselines, and resource management patterns.

## Executive Summary

Ralph uses a bounded execution history to prevent unbounded memory growth during long-running pipelines. All production channels are bounded, Arc usage follows clear ownership hierarchies with no circular references, and unsafe code is limited to Unix process management primitives with comprehensive safety documentation and testing.

## Memory Budget

### Execution History Bounds

- **Default limit:** 1000 entries (config: `execution_history_limit`)
- **Memory per entry (history heap):** ~53 bytes (measured baseline)
- **Total execution history heap at limit:** ~51 KB (1000 entries)
- **Checkpoint size with bounded history:** ~375 KB serialized (1000 entries)

Note: these memory numbers describe the heap used by the execution history buffer itself. Total process memory also includes the rest of `PipelineState`, parser buffers, and other runtime allocations. See `docs/performance/memory-budget.md` for the full measured breakdown.

**Rationale:** Execution history grows with every pipeline step (agent invocations, state transitions, validation steps). Without bounds, a pipeline with 10,000 steps would consume ~530 KB of additional heap just for the history buffer (based on the ~53 bytes/entry baseline). The 1000-entry limit provides sufficient debugging context while keeping memory bounded.

**Implementation:** `PipelineState::add_execution_step(step, limit)` enforces the limit by dropping oldest entries when capacity is reached (ring buffer behavior).

**Location:** `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395`

### Performance Baselines

Established by benchmarks in `ralph-workflow/src/benchmarks/`:

| Operation | Baseline | Ceiling (Regression Detector) |
|-----------|----------|-------------------------------|
| Checkpoint serialization (1000 entries) | <50ms | 100ms |
| Checkpoint deserialization (1000 entries) | <30ms | 100ms |
| Round-trip (serialize + deserialize) | <80ms | 200ms |
| Checkpoint size (bounded history) | ~375 KB | 1 MB |
| Memory growth per iteration | ~500 bytes | N/A (bounded) |
| Checkpoint size stability across cycles | ±5% | N/A |

**Regression Detection:** The `benchmark_serialization_performance_ceiling` test in `checkpoint_serialization.rs` will fail if performance regresses significantly, indicating a potential issue with the bounded implementation.

**Baseline Measurement:** Run benchmarks with `--nocapture` to see detailed output:
```bash
cargo xtask test -p ralph-workflow benchmarks -- --nocapture
```

## Arc Usage Patterns

### Verified Safe Patterns

All Arc usage in Ralph follows clear ownership hierarchies with no circular references:

1. **Workspace cloning:** `Arc<MemoryWorkspace>` and `Arc<WorkspaceFs>` 
   - Pattern: Passed down from `AppContext` → `PipelineContext` → `PhaseContext`
   - Ownership: One-way flow, no back-references
   - Verification: Tests in `memory_safety::arc_patterns`

2. **Executor cloning:** `Arc<dyn ProcessExecutor>`
   - Pattern: Shared across pipeline phases via context
   - Ownership: Read-only trait object, no interior mutability
   - Verification: Tests confirm strong_count returns to baseline after pipeline completion

3. **Logger/Printer:** Shared logging utilities
   - Pattern: Independent lifetime from logged resources
   - Ownership: No bidirectional references with workspace or state
   - Verification: Arc count tests verify no leaks

### Arc Policy

**Production code MUST maintain clear ownership hierarchies:**
- No parent-child bidirectional Arc references
- No cache structures that hold Arc to objects that hold Arc back to cache
- All Arc usage should allow strong_count to return to baseline after cleanup

**Verification:** All Arc patterns are verified by tests in `tests/integration_tests/memory_safety/arc_patterns.rs`

### Arc Audit Results

**Total Arc usage:** 880+ occurrences across 71 files

**Audit findings (Feb 2026):**
- ✅ All patterns follow dependency injection via trait objects (`Arc<dyn Trait>`)
- ✅ No circular references detected
- ✅ Clear ownership flow: `AppContext` → `PipelineContext` → `PhaseContext` → phase functions
- ✅ Tests verify Arc cleanup after pipeline completion

## Channel Bounds

### Production Channel Policy

**All production channels MUST use `sync_channel` with explicit capacity:**

```rust
// ✅ CORRECT: Bounded channel
use std::sync::mpsc;
let (tx, rx) = mpsc::sync_channel(capacity);

// ❌ WRONG: Unbounded channel (only acceptable in tests)
let (tx, rx) = mpsc::channel();
```

### Verified Bounded Channels

**Production code with bounded channels:**

1. **BoundedEventQueue** (`json_parser/event_queue/bounded_queue.rs:84`)
   - Uses: `sync_channel` with configurable capacity
   - Behavior: Blocks sender when full (backpressure)
   - Typical capacity: 500 events
   - Verified by: `memory_safety::channel_bounds` tests

2. **Stdout pump buffering** (`pipeline/prompt/streaming.rs`)
   - Uses: `sync_channel` with explicit bounded capacity
   - Behavior: Backpressure when parsing falls behind pumping
   - Rationale: Caps buffering (prevents unbounded memory growth) while keeping the pump thread simple
   - Memory guarantee: Buffer size capped at (chunk_size * channel_capacity)
   - Verified by: `memory_safety::channel_bounds::test_streaming_output_channel_pattern`

### Acceptable Channel Exceptions

Some channel producers must not block (e.g., library callbacks). In those cases we still
prefer bounded channels, but use non-blocking send with explicit drop-on-full semantics.

1. **File system monitoring** (`files/protection/monitoring.rs:126`)
   - Uses: Bounded `sync_channel` queue + `try_send` (drop-on-full)
   - Reason: `notify` callback must not block; bounded queue keeps memory capped
   - Risk mitigation: Dropped events are acceptable (events are coalescable; polling fallback covers misses)
   - Verification: Monitor thread lifetime tied to pipeline run

**Policy exception criteria:**
- Bounded queue would introduce deadlock risk OR callback must not block
- Backpressure is handled explicitly (block, drop-on-full, or coalescing)
- Consumer drains continuously
- Thread lifecycle is properly managed

## Thread Lifecycle

### Thread Cleanup Policy

**All production threads MUST have documented cleanup strategy:**

1. **Normal operation:** Thread should be joined before function returns
2. **Timeout scenarios:** Best-effort join with reasonable deadline (e.g., 2 seconds)
3. **Panic scenarios:** Document whether thread is joined or detached

### Documented Tradeoffs

**File monitor thread** (`monitoring.rs:357-366`):
```rust
// Documented tradeoff: Thread not joined in Drop to avoid panic-during-panic
// "Take the handle and let it finish on its own
//  (we can't wait in Drop because we might be panicking)"
```
- Acceptable because: Monitor lifetime is tied to pipeline run
- Verification: Tests confirm no hangs in normal operation
- Risk: Thread may outlive Drop in panic scenarios (acceptable tradeoff)

**Streaming pump thread** (`streaming.rs:176-183`):
```rust
// Best-effort join with 2-second deadline
// Thread is detached if not finished by deadline
```
- Acceptable because: Pump threads are benign background workers
- Verification: Tests confirm pipeline completes without hanging
- Risk: Thread may be detached on timeout (acceptable because pump is benign)

### Thread Lifecycle Tests

All thread cleanup scenarios are verified by tests in `tests/integration_tests/memory_safety/thread_lifecycle.rs`:
- Pipeline completes without hanging (no zombie threads)
- Multiple runs don't accumulate threads
- Error scenarios clean up properly
- Timeout scenarios don't cause hangs

## Unsafe Code

### Unsafe Code Policy

**Unsafe code is limited to Unix process management primitives** and MUST:
1. Have clear safety documentation
2. Proper error handling
3. Platform-specific guards (`#[cfg(unix)]`)
4. Behavioral tests verifying correctness

### Unsafe Code Locations

**All unsafe code is in `executor/real.rs`:**

1. **Non-blocking file descriptors** (lines 16-25):
   ```rust
   unsafe {
       let flags = libc::fcntl(fd, libc::F_GETFL);
       if flags < 0 {
           return Err(io::Error::last_os_error());
       }
       if libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
           return Err(io::Error::last_os_error());
       }
   }
   ```
   - Purpose: Make stdout/stderr non-blocking for cancellable streaming
   - Safety: Valid fd owned by process, proper error handling
   - Verification: Tests in `memory_safety::unsafe_patterns`

2. **Process cleanup** (lines 40-56):
   ```rust
   unsafe {
       let _ = libc::kill(-pid, libc::SIGTERM);  // Process group
       let _ = libc::kill(pid, libc::SIGTERM);   // Single process
   }
   ```
   - Purpose: Terminate process and its children
   - Safety: Fallback with SIGTERM then SIGKILL, proper timeouts
   - Verification: Tests confirm clean process termination

3. **Process group setup** (lines 164-172):
   ```rust
   unsafe {
       cmd.pre_exec(|| {
           if libc::setpgid(0, 0) != 0 {
               return Err(io::Error::last_os_error());
           }
           Ok(())
       });
   }
   ```
   - Purpose: Put agent in its own process group for clean timeout enforcement
   - Safety: setpgid(0, 0) puts current process in new group, proper error handling
   - Verification: Tests confirm process spawns and terminates correctly

### Unsafe Code Verification

All unsafe code is verified through behavioral tests that confirm:
- Normal operation succeeds without crashes
- Error cases are handled gracefully
- No segfaults or undefined behavior under stress
- Platform-specific code is properly guarded

Tests: `tests/integration_tests/memory_safety/unsafe_patterns.rs`

## Verification

Memory safety is verified by multiple layers:

### 1. Integration Tests

Location: `tests/integration_tests/memory_safety/`

Modules:
- `bounded_growth.rs` - Execution history remains bounded
- `arc_patterns.rs` - No circular Arc references
- `channel_bounds.rs` - Channels are properly bounded
- `thread_lifecycle.rs` - Threads clean up correctly
- `unsafe_patterns.rs` - Unsafe code behaves safely

### 2. Benchmark Tests

Location: `ralph-workflow/src/benchmarks/`

Modules:
- `memory_usage.rs` - Memory growth patterns and stability
- `checkpoint_serialization.rs` - Serialization performance and size

Run with: `cargo xtask test -p ralph-workflow benchmarks -- --nocapture`

### 3. Verification

Memory safety verification is included in the unified xtask runner.

Run before every commit:
```bash
cargo xtask verify
```

### 4. CI Integration

Memory safety verification runs in CI pipeline (`.woodpecker.yml`) to prevent regressions.

## Future Improvements

Potential enhancements for long-running pipeline monitoring:

1. **Memory profiling integration**
   - Add jemalloc for precise allocation tracking
   - Memory usage graphs in CI

2. **Configurable history limits**
   - Allow users to configure execution history limit
   - Tradeoff: memory vs. debugging context

3. **Checkpoint optimization**
   - Delta checkpoints (only changes since last checkpoint)
   - Binary serialization format (faster than JSON)
   - Compression for large checkpoints

4. **Advanced monitoring**
   - Per-run memory high-water mark tracking
   - Alerts for unusual growth patterns
   - Production memory profiling hooks

## References

- **Bounded execution history implementation:** `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395`
- **Checkpoint serialization:** `ralph-workflow/src/checkpoint/execution_history.rs`
- **Arc patterns:** All usage of `Arc<dyn Trait>` follows dependency injection pattern
- **Channel usage:** `json_parser/event_queue/bounded_queue.rs`, `pipeline/prompt/streaming.rs`, `files/protection/monitoring.rs`
- **Unsafe code:** `executor/real.rs` (Unix process management only)
- **Tests:** `tests/integration_tests/memory_safety/` and `ralph-workflow/src/benchmarks/`
