# Performance Documentation

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

### Verification Commands

```bash
# Full verification suite (includes memory safety and performance regression)
cargo xtask verify

# Long-running pipeline tests (10k+ iterations)
cargo test -p ralph-workflow-tests --test integration_tests \
    memory_safety::long_running_pipeline

# Performance baseline verification
cargo test -p ralph-workflow-tests --test integration_tests \
    memory_safety::bounded_growth::test_execution_history_heap_size_within_baseline
```

### Key Implementation Files

- `ralph-workflow/src/reducer/state/pipeline/core_state.rs:395-403` -
  Bounded execution history implementation
- `ralph-workflow/src/benchmarks/` - Performance measurement benchmarks
- `tests/integration_tests/memory_safety/` - Memory safety verification tests

## Contributing

When making changes that affect memory usage or performance:

1. Run full verification suite (see above)
2. Update baselines if intentional performance characteristics change
3. Update this documentation if new patterns are introduced
4. Ensure CI regression tests pass

## See Also

- `AGENTS.md` - General contribution guidelines
- `docs/agents/verification.md` - Required verification before PR/completion
- `tests/INTEGRATION_TESTS.md` - Integration test style guide
