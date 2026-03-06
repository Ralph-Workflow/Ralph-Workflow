# Scripts

All verification is now performed by the unified Rust xtask runner.

## Canonical verification command

```bash
cargo xtask verify
```

See [docs/agents/verification.md](../docs/agents/verification.md) for the full reference.

The shell scripts previously in this directory have been migrated into `xtask/src/verify.rs` and `xtask/src/compliance.rs`.
