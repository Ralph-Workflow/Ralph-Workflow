# Remote Build Server

All `cargo xtask` subcommands automatically run on the least-loaded available build server (`rw-build-server` or `rw-build-server-2`). No flags, no setup beyond SSH key auth on both servers.

**Use `cargo xtask` for everything.** Direct `cargo test`, `cargo clippy`, `cargo check`, etc. run locally and overheat your laptop. `cargo xtask` auto-dispatches to the best available remote build server. The only exception is `cargo build` for producing a local macOS executable (e.g., via Makefile).

---

## How it works

1. **Probe** — SSH to both `rw-build-server` and `rw-build-server-2` in parallel with a 5-second timeout (`BatchMode=yes`, no password prompt), fetching `/proc/loadavg` from each.
2. **Select** — Picks the server with the lower 1-minute load average. If loads are within `0.1` of each other they are treated as equivalent and one is chosen pseudo-randomly. If only one server responds, it is used unconditionally. If both are unreachable, falls back to local execution.
3. **Sync** — `rsync -az --delete --exclude=.git/ --filter=':- .gitignore'` sends the working tree to the selected server. Delta transfers make subsequent syncs fast (~1-3s on unchanged trees).
4. **Git init** — Initializes a minimal git repo on the remote (`git init` + `git add -A` + `git commit`). Tests using libgit2 or `git rev-parse` require a valid repo; rsync excludes `.git/` so this step is essential.
5. **Execute** — `ssh -t <selected-server> "cd <remote_root> && cargo xtask <args>"`. Output streams to the local terminal in real time via pseudo-TTY.
6. **Exit code** — Propagated from the remote command to the local shell.
7. **Fallback** — If both probes fail (both servers unreachable), prints `[remote-build] no build server reachable, running locally` and runs locally.

### Remote path

`/tmp/rw-<first-16-hex-chars of SHA-256(git-root + hostname + server)>`

The hash makes the path unique per (local repo, machine, server) triple and stable across invocations, so incremental rsyncs and `target/` caches are preserved per server. Each build server maintains its own independent incremental build cache — switching between servers is seamless. Paths live in `/tmp/` so the OS cleans them up automatically.

### Skip conditions

- CWD starts with `/tmp` — already running on a build server (prevents infinite re-dispatch).
- `XTASK_LOCAL=1` — **emergency fallback only** (see below).

---

## Usage

**Always use `cargo xtask <subcommand>` instead of direct `cargo <subcommand>`.** Every xtask subcommand auto-dispatches to the remote build server.

### Verification and linting

```bash
cargo xtask verify          # full verification (11+ checks)
cargo xtask verify --gui    # includes GUI/frontend/release checks
cargo xtask dylint           # custom lint checks
cargo xtask dylint --verbose # with detailed output
cargo xtask coverage         # code coverage (diagnostic)
cargo xtask lsp-forbidden-allow-expect
cargo xtask dylint-report
```

### Testing (replaces `cargo test`)

```bash
cargo xtask test -p ralph-workflow --lib
cargo xtask test -p ralph-workflow-tests --test integration_tests
cargo xtask test -p xtask
cargo xtask test -p ralph-workflow --lib -- some_test_name
```

### Clippy, fmt, check, bench (replaces direct cargo commands)

```bash
cargo xtask clippy -p ralph-workflow -- -D warnings
cargo xtask fmt --all --check
cargo xtask check -p ralph-workflow
cargo xtask bench -p ralph-workflow
```

### Building a local executable (the one exception)

Direct `cargo build` is only for producing a **macOS executable** to run locally. This is the only case where running cargo directly (not through xtask) is appropriate:

```bash
cargo build --release    # local macOS binary
```

For remote compile-checking without needing a local binary, use:
```bash
cargo xtask build --release
```

### Sync only (no execution)

```bash
./scripts/remote/sync.sh
```

---

## Do NOT use direct `cargo test` / `cargo clippy` / `cargo check`

Direct cargo commands run locally, which:
- Saturates your laptop's CPU and overheats it
- Produces slower results (no warm remote `target/` cache)
- Runs on macOS instead of the Linux CI environment

**Always use `cargo xtask <cmd>` instead.** The xtask passthrough subcommands (`test`, `build`, `clippy`, `fmt`, `check`, `bench`) forward all arguments to `cargo <cmd>` on the remote.

---

## `XTASK_LOCAL=1` — Emergency Only

**`XTASK_LOCAL=1` must ONLY be used when both build servers are confirmed unreachable** (network down, servers offline, SSH broken).

**Never use it to:**
- Work around a test failure
- Save time or for convenience
- Debug something "quickly" locally
- Avoid waiting for the SSH probe

```bash
# EMERGENCY ONLY:
XTASK_LOCAL=1 cargo xtask verify
```

---

## Prerequisites

### SSH config

Both `rw-build-server` and `rw-build-server-2` must be configured in `~/.ssh/config` with key auth:

```
Host rw-build-server
  HostName <ip-or-hostname>
  User <user>
  IdentityFile ~/.ssh/<key>
  ServerAliveInterval 60
  ConnectTimeout 5

Host rw-build-server-2
  HostName <ip-or-hostname-2>
  User <user>
  IdentityFile ~/.ssh/<key>
  ServerAliveInterval 60
  ConnectTimeout 5
```

`ConnectTimeout 5` caps the fallback latency when a server is down. If one server is unreachable, the other is used automatically with no manual intervention.

### Remote toolchain

See `docs/plans/2026-04-08-remote-build-server.md` section 5 for one-time remote machine setup (Rust, bun, cargo-dylint, system dependencies). Apply the same setup to `rw-build-server-2`.

---

## Build acceleration

The remote build servers use several tools to speed up compilation:

### mold linker

[mold](https://github.com/rui314/mold) is a modern linker that is 3-10x faster than the default GNU ld for linking Rust binaries. It is configured automatically via `.cargo/config.toml` for the `x86_64-unknown-linux-gnu` target — no manual setup needed on new machines beyond `sudo apt-get install -y mold clang`.

### sccache

[sccache](https://github.com/mozilla/sccache) caches compiled crate artifacts across builds. It helps when switching between debug/release/clippy modes or when the target directory is wiped. The `rustc-wrapper-dylint` script automatically chains through sccache when it is available on PATH. Install with `cargo install sccache --locked`.

### Cargo profile tuning

The workspace `Cargo.toml` includes several profile optimizations:

- **`[profile.dev.package."*"]` opt-level = 2**: Dependencies are compiled with optimizations even in dev mode. Since deps rarely change and are cached, this is nearly free on incremental builds but makes test execution significantly faster.
- **`[profile.test]` opt-level = 1**: Light optimization for test execution speed.
- **`[profile.dev]` split-debuginfo = "unpacked"**: Reduces macOS debug build link times.
- **`[profile.release-verify]`**: Uses thin LTO instead of full LTO for verification builds. The `cargo xtask verify` release lane uses this profile — it catches the same link errors as full LTO at a fraction of the compile cost.

### Compilation/test performance assessment

The codebase is already highly optimized for compilation and test speed:
- **mold linker** (3-10x faster linking vs GNU ld)
- **sccache** artifact caching across builds
- **`codegen-units=256`** with incremental compilation in dev profile
- **7-lane parallel verification** with warm-run caching
- **Separate `target/` directories** per verification lane to prevent contention
- **cargo-nextest** with `num-cpus` thread parallelism
- **Three independent integration test compilation units** to maximize parallelism

The remaining potential optimization — the Cranelift codegen backend — requires stable Cargo support for the `codegen-backend` feature that is not yet shipped. No further meaningful compilation or test speed improvements are available without major architectural changes.

---

## Build acceleration

The remote build server uses several tools to speed up compilation:

### mold linker

[mold](https://github.com/rui314/mold) is a modern linker that is 3-10x faster than the default GNU ld for linking Rust binaries. It is configured automatically via `.cargo/config.toml` for the `x86_64-unknown-linux-gnu` target — no manual setup needed on new machines beyond `sudo apt-get install -y mold clang`.

### sccache

[sccache](https://github.com/mozilla/sccache) caches compiled crate artifacts across builds. It helps when switching between debug/release/clippy modes or when the target directory is wiped. The `rustc-wrapper-dylint` script automatically chains through sccache when it is available on PATH. Install with `cargo install sccache --locked`.

### Cargo profile tuning

The workspace `Cargo.toml` includes several profile optimizations:

- **`[profile.dev.package."*"]` opt-level = 2**: Dependencies are compiled with optimizations even in dev mode. Since deps rarely change and are cached, this is nearly free on incremental builds but makes test execution significantly faster.
- **`[profile.test]` opt-level = 1**: Light optimization for test execution speed.
- **`[profile.dev]` split-debuginfo = "unpacked"**: Reduces macOS debug build link times.
- **`[profile.release-verify]`**: Uses thin LTO instead of full LTO for verification builds. The `cargo xtask verify` release lane uses this profile — it catches the same link errors as full LTO at a fraction of the compile cost.

---

## Platform note

The remote machines run **Debian Linux (x86_64)**. `cargo xtask verify` produces no binary artifacts that need copy-back, so the macOS/Linux difference is transparent for verification and testing. Linux binaries cannot run on macOS — the primary value of remote builds is compile-time checking and test execution, not producing runnable local binaries.

---

## Troubleshooting

### "no build server reachable, running locally"

This means both SSH probes failed. Check:
- Network connectivity (`ping <server-ip>`)
- SSH key auth (`ssh rw-build-server echo ok` and `ssh rw-build-server-2 echo ok`)
- `~/.ssh/config` entries exist and are correct for both servers
- If only one server is unreachable, the other is used automatically — no action needed

### Tests fail with "could not find repository"

The `ensure_remote_git_repo` step may have failed silently. SSH into the selected remote and check:
```bash
ssh rw-build-server "cd /tmp/rw-<hash> && git status"
```
If there's no `.git/`, run `git init && git add -A && git commit -m init` manually.

### Toolchain mismatch / dylint errors

The project pins `nightly-2026-02-04` in `rust-toolchain.toml`. Ensure the remote has this toolchain:
```bash
ssh rw-build-server "rustup toolchain list | grep nightly-2026-02-04"
ssh rw-build-server-2 "rustup toolchain list | grep nightly-2026-02-04"
```
If missing: `ssh rw-build-server "rustup toolchain install nightly-2026-02-04"`

### cargo-dylint version mismatch

The project pins cargo-dylint to 3.5.1. Check the remote:
```bash
ssh rw-build-server "cargo dylint --version"
ssh rw-build-server-2 "cargo dylint --version"
```
If wrong version: `ssh rw-build-server "cargo install cargo-dylint dylint-link --version 3.5.1"`

### PATH issues on remote (bun/cargo not found)

SSH non-interactive sessions may have a stripped PATH. Ensure `~/.bashrc` on each remote sources `~/.cargo/env` and adds `~/.bun/bin` for non-interactive sessions:

```bash
# Near the top of ~/.bashrc on rw-build-server and rw-build-server-2:
case $- in
  *i*) ;;
  *) source ~/.cargo/env; export PATH="$HOME/.bun/bin:$PATH" ;;
esac
```
