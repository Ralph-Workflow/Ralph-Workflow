# Performance Baselines

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This directory contains baseline benchmark outputs for detecting performance regressions.

## Baseline Files

- **memory-usage-baseline.txt**: Memory growth benchmarks (10, 100, 1000 iterations)
- **checkpoint-serialization-baseline.txt**: Serialization performance benchmarks
- **combined-baseline.txt**: All benchmarks combined

## Usage

### Detecting Regressions

After making changes that might affect performance, compare current benchmarks against baselines:

```bash
# Run current benchmarks
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture > current.txt

# Compare with baseline
diff docs/performance/baselines/combined-baseline.txt current.txt
```

### Updating Baselines

When intentional performance improvements are made, update the baselines:

```bash
# Update memory usage baseline
cargo xtask test -p ralph-workflow --lib benchmarks::memory_usage -- --nocapture \
  > docs/performance/baselines/memory-usage-baseline.txt

# Update checkpoint serialization baseline
cargo xtask test -p ralph-workflow --lib benchmarks::checkpoint_serialization -- --nocapture \
  > docs/performance/baselines/checkpoint-serialization-baseline.txt

# Update combined baseline
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture \
  > docs/performance/baselines/combined-baseline.txt
```

## Key Metrics to Monitor

### Memory Growth

- **10 iterations**: ~530 bytes heap
- **100 iterations**: ~5.3 KB heap
- **1000 iterations**: ~51 KB heap

**Red flag**: If growth rate exceeds ~60 bytes/entry or total size significantly higher

### Checkpoint Serialization

- **10 entries**: ~170 µs, 7 KB
- **100 entries**: ~900 µs, 41 KB
- **1000 entries**: ~7.7 ms, 375 KB

**Red flag**: If serialization time doubles or checkpoint size exceeds 500 KB for 1000 entries

### Scaling Characteristics

- Memory growth should be **linear** (constant bytes per entry)
- Serialization time should be **linear** (no quadratic behavior)
- No performance cliffs at specific sizes

## Acceptable Variance

Small variations are expected due to:
- System load during benchmarking
- CPU frequency scaling
- Memory allocator behavior
- Background processes

**Guidelines**:
- ±10% variance in timing measurements is normal
- ±5% variance in memory measurements is normal
- Larger variances require investigation

## When to Update Baselines

Update baselines when:
1. **Intentional optimization**: You've improved performance
2. **Data structure changes**: You've changed ExecutionStep or PipelineState
3. **Serialization format changes**: You've modified checkpoint format
4. **Significant refactoring**: Major changes to pipeline implementation

**Do NOT update baselines** to hide performance regressions.

## CI Integration (Future Enhancement)

These baselines can be integrated into CI to automatically detect regressions:

```bash
# In CI pipeline
cargo xtask test -p ralph-workflow --lib benchmarks -- --nocapture > ci-benchmarks.txt

# Compare (allowing 10% variance)
# Exit with error if regression detected beyond threshold
```

## Related Documentation

- **Memory budget**: `../memory-budget.md` - Comprehensive performance documentation
- **Verification**: `../../agents/verification.md` - Required verification commands
- **Memory safety tests**: `../../../tests/integration_tests/memory_safety/` - Integration tests

## Baseline Creation Date

These baselines were created on **2026-02-13** during the memory safety and resource management audit.

**System**: Darwin (macOS)
**Rust version**: Check with `rustc --version`
**Cargo version**: Check with `cargo --version`

For reproducible benchmarks, ensure similar system configuration.
