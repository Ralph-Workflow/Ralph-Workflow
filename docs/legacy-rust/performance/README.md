# Performance Documentation (Historical - Rust Era)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **This file describes the retired Rust implementation and is kept for archival reference only.**
> The Python package does not share the same performance characteristics, commands, or code paths.
> For Python performance guidance, refer to the package docstrings and Sphinx documentation.

This directory contains documentation related to performance characteristics,
resource management, and monitoring for the Ralph pipeline.

## Documents

- **[Memory Budget](./memory-budget.md)** - Expected memory usage patterns and
  bounded growth mechanisms
- **[Monitoring Guide](./monitoring-guide.md)** - Production memory monitoring
  and troubleshooting
- **[Optimization Guide](./optimization-guide.md)** - Performance optimization
  patterns and best practices

## Quick Reference

### Expected Memory Usage

| Component | Typical | Maximum |
|-----------|---------|---------|
| Execution History (heap) | ~50-60 KB | ~72 KB |
| Checkpoint (serialized) | 300-400 KB | 2 MB |
| Total per run | ~1-10 MB | Varies (file snapshots/logging) |

### Verification Commands (Historical - Rust Era)

These commands are from the retired Rust implementation:

```bash
# Full verification suite (includes memory safety and performance regression)
cargo xtask verify

# Long-running pipeline tests (10k+ iterations)
cargo xtask test -p ralph-workflow-tests --test integration_tests \
    memory_safety::long_running_pipeline

# Performance baseline verification
cargo xtask test -p ralph-workflow-tests --test integration_tests \
    memory_safety::bounded_growth::test_execution_history_heap_size_within_baseline
```

### Key Implementation Files (Historical)

These files are from the retired Rust implementation:

- `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395-403` -
  Bounded execution history implementation (Rust)
- `ralph-workflow/src/benchmarks/` - Performance measurement benchmarks (Rust)
- `tests/integration_tests/memory_safety/` - Memory safety verification tests (Rust)

## See Also

- `AGENTS.md` - General contribution guidelines
- `docs/agents/verification.md` - Required verification before PR/completion (Python)
