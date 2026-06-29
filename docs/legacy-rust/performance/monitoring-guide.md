# Production Memory Monitoring Guide (Historical - Rust Era)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **This file describes the retired Rust implementation and is kept for archival reference only.**
> The Python package does not share the same monitoring approach, commands, or code paths.
> For Python monitoring guidance, refer to the package docstrings and Sphinx documentation.

This guide explains how to monitor memory usage in production deployments
to detect issues early and prevent OOM failures.

## Enabling Memory Monitoring

Build with the `monitoring` feature flag:

```bash
cargo build --release --features monitoring
```

## Memory Metrics API

When `monitoring` feature is enabled, the `memory_metrics` module provides:

```rust
use ralph_workflow::monitoring::memory_metrics::{MemoryMetricsCollector, MemorySnapshot};

// In your pipeline execution:
let mut metrics = MemoryMetricsCollector::new(100); // snapshot every 100 iterations

// After each iteration:
metrics.maybe_record(&state);

// Export metrics for analysis:
let json = metrics.export_json()?;
std::fs::write("memory_metrics.json", json)?;
```

## Metrics Schema

Each snapshot contains:

```json
{
  "iteration": 1500,
  "execution_history_len": 1000,
  "execution_history_heap_bytes": 487234,
  "checkpoint_count": 15,
  "timestamp": "2024-02-13T10:30:45Z"
}
```

## Alert Thresholds

Set up alerts based on these thresholds:

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| execution_history_len | == execution_history_limit | > execution_history_limit | At-cap is expected on long runs; increase the limit if you need more history. Exceeding the limit indicates a bug or misconfiguration. |
| execution_history_heap_bytes | >600 KB | >1 MB | Investigate history content |
| Checkpoint interval | >30 min | >60 min | Check checkpoint performance |

## Detecting Memory Leaks

### Symptom 1: Unbounded History Growth

**Detection:**
```bash
# Check if history length exceeds the configured limit.
# Default execution_history_limit is 1000; replace 1000 if you changed it.
jq '.execution_history_len > 1000' memory_metrics.json
```

**Root Cause:** `add_execution_step()` not called with limit parameter

**Fix:** Ensure all code paths use bounded method

### Symptom 2: Increasing Heap Size

**Detection:**
```bash
# Check for monotonic increase in heap size
jq '[.[] | .execution_history_heap_bytes] | . == sort' memory_metrics.json
```

**Root Cause:** History entries contain growing data (large outputs)

**Fix:** Truncate large outputs before adding to history

### Symptom 3: Memory Growth Despite Bounded History

**Detection:**
```bash
# Compare heap size at different iterations with same history length
jq '.[] | select(.execution_history_len == 1000) | .execution_history_heap_bytes' memory_metrics.json
```

**Root Cause:** Memory leak outside execution history

**Fix:** Run full memory safety verification suite

## Tools and Commands

### Check Current Memory Usage (Linux)

```bash
# Get RSS (Resident Set Size) for ralph process
ps aux | grep ralph | awk '{print $6/1024 " MB"}'
```

### Profile Memory Usage (valgrind)

```bash
valgrind --tool=massif --massif-out-file=massif.out ralph
ms_print massif.out > memory_profile.txt
```

### Continuous Monitoring (Prometheus)

Export metrics to Prometheus format (requires custom exporter):

```rust
// Example Prometheus gauge
execution_history_length{phase="development"} 1000
execution_history_heap_bytes{phase="development"} 487234
```

## Troubleshooting

See [Memory Budget](./memory-budget.md) for expected usage patterns.
