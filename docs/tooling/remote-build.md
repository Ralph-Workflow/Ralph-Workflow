# Remote Build Server

All `cargo xtask` subcommands automatically run on `rw-build-server`. No flags, no setup beyond SSH key auth.

**Use `cargo xtask` for everything.** Direct `cargo test`, `cargo clippy`, `cargo check`, etc. run locally and overheat your laptop. `cargo xtask` auto-dispatches to the remote build server. The only exception is `cargo build` for producing a local macOS executable (e.g., via Makefile).

---

## How it works

1. **Probe** — SSH to `rw-build-server` with 5-second timeout (`BatchMode=yes`, no password prompt).
2. **Sync** — `rsync -az --delete --exclude=.git/ --filter=':- .gitignore'` sends the working tree. Delta transfers make subsequent syncs fast (~1-3s on unchanged trees).
3. **Git init** — Initializes a minimal git repo on the remote (`git init` + `git add -A` + `git commit`). Tests using libgit2 or `git rev-parse` require a valid repo; rsync excludes `.git/` so this step is essential.
4. **Execute** — `ssh -t rw-build-server "cd <remote_root> && cargo xtask <args>"`. Output streams to the local terminal in real time via pseudo-TTY.
5. **Exit code** — Propagated from the remote command to the local shell.
6. **Fallback** — If the probe fails (server unreachable), prints `[remote-build] rw-build-server unreachable, running locally` and runs locally.

### Remote path

`/tmp/rw-<first-16-hex-chars of SHA-256(git-root + hostname)>`

The hash makes the path unique per (local repo, machine) pair and stable across invocations, so incremental rsyncs and `target/` caches are preserved. Lives in `/tmp/` so the OS cleans it up automatically.

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

**`XTASK_LOCAL=1` must ONLY be used when `rw-build-server` is confirmed unreachable** (network down, server offline, SSH broken).

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

`rw-build-server` must be configured in `~/.ssh/config` with key auth:

```
Host rw-build-server
  HostName <ip-or-hostname>
  User <user>
  IdentityFile ~/.ssh/<key>
  ServerAliveInterval 60
  ConnectTimeout 5
```

`ConnectTimeout 5` caps the fallback latency when the server is down.

### Remote toolchain

See `docs/plans/2026-04-08-remote-build-server.md` section 5 for one-time remote machine setup (Rust, bun, cargo-dylint, system dependencies).

---

## Platform note

The remote machine runs **Debian Linux (x86_64)**. `cargo xtask verify` produces no binary artifacts that need copy-back, so the macOS/Linux difference is transparent for verification and testing. Linux binaries cannot run on macOS — the primary value of remote builds is compile-time checking and test execution, not producing runnable local binaries.

---

## Troubleshooting

### "rw-build-server unreachable, running locally"

This means the SSH probe failed. Check:
- Network connectivity (`ping <server-ip>`)
- SSH key auth (`ssh rw-build-server echo ok`)
- `~/.ssh/config` entry exists and is correct

### Tests fail with "could not find repository"

The `ensure_remote_git_repo` step may have failed silently. SSH into the remote and check:
```bash
ssh rw-build-server "cd /tmp/rw-<hash> && git status"
```
If there's no `.git/`, run `git init && git add -A && git commit -m init` manually.

### Toolchain mismatch / dylint errors

The project pins `nightly-2026-02-04` in `rust-toolchain.toml`. Ensure the remote has this toolchain:
```bash
ssh rw-build-server "rustup toolchain list | grep nightly-2026-02-04"
```
If missing: `ssh rw-build-server "rustup toolchain install nightly-2026-02-04"`

### cargo-dylint version mismatch

The project pins cargo-dylint to 3.5.1. Check the remote:
```bash
ssh rw-build-server "cargo dylint --version"
```
If wrong version: `ssh rw-build-server "cargo install cargo-dylint dylint-link --version 3.5.1"`

### PATH issues on remote (bun/cargo not found)

SSH non-interactive sessions may have a stripped PATH. Ensure `~/.bashrc` on the remote sources `~/.cargo/env` and adds `~/.bun/bin` for non-interactive sessions:

```bash
# Near the top of ~/.bashrc on rw-build-server:
case $- in
  *i*) ;;
  *) source ~/.cargo/env; export PATH="$HOME/.bun/bin:$PATH" ;;
esac
```
