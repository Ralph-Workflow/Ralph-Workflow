# Integration Test Reference

> **Canonical guide:** [docs/agents/testing-guide.md](../docs/agents/testing-guide.md)
>
> All rules, patterns, anti-patterns, and examples live there. This file is a
> redirect stub kept for discoverability from the `tests/` directory.

## Quick Reference

| Tier | Binary | Parallelism | Target runtime |
|------|--------|-------------|----------------|
| Unit | `cargo test -p ralph-workflow --lib` | parallel | < 10 s |
| Integration | `cargo test -p ralph-workflow-tests --test integration_tests` | parallel | < 60 s wall-clock |
| Process system | `cargo test -p ralph-workflow-tests --test process-system-tests` | parallel | manual only |
| git2 system | `cargo test -p ralph-workflow-tests --test system_tests` | serial (libgit2) | manual only |
