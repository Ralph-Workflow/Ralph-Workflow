# Remote Build Server Plan

**Date:** 2026-04-08
**Status:** Implemented
**Scope:** Offload `cargo xtask verify`, `cargo build`, and `cargo test` to a dedicated remote build machine (`rw-build-server`), with automatic fallback to local execution when the remote is unreachable.

> **Implementation note:** All `cargo xtask` subcommands now auto-dispatch to the remote via `try_run_remote()` in `xtask/src/boundary/remote.rs`, wired into `run_from_env()`. Shell scripts in `scripts/remote/` extend this to arbitrary commands. See `docs/tooling/remote-build.md` for the developer-facing guide.

---

## 1) Motivation

`cargo xtask verify` runs 7 concurrent lanes (fmt, clippy, unit tests, integration tests, release build, dylint, frontend) and is CPU/RAM intensive. Running it locally blocks the workstation for minutes per invocation. A persistent remote build machine with a warm `target/` cache drastically reduces wall-clock time and leaves local resources free.

---

## 2) Assumptions

- The remote machine is already provisioned and accessible as `rw-build-server` in `~/.ssh/config`.
- The remote has the full toolchain installed: Rust stable + nightly, bun, cargo-dylint, dylint-link, libgit2, build-essential.
- SSH key auth is configured (no password prompt).
- The remote runs Linux (x86_64 or aarch64); cross-compilation is out of scope for this plan.

`~/.ssh/config` entry (already expected to exist):
```
Host rw-build-server
  HostName <ip-or-hostname>
  User <user>
  IdentityFile ~/.ssh/<key>
  ServerAliveInterval 60
  ConnectTimeout 5
```

The `ConnectTimeout 5` is important — it sets the window for the availability probe so fallback to local is fast.

---

## 3) Design

### 3.1 Availability Probe

Before every remote invocation, run a cheap SSH probe:

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes rw-build-server exit 0 2>/dev/null
```

- Exit 0 → remote is available, proceed remotely.
- Non-zero → remote is unreachable, fall back to local silently (or with a single warning line).

This probe adds at most 5 seconds to a cold-miss fallback, and near-zero overhead on a warm hit.

### 3.2 Code Sync

Use `rsync` over SSH before every remote invocation. Rsync's delta algorithm means subsequent syncs after the first are fast (only changed files transfer).

```bash
rsync -az --delete \
  --exclude=.git/ \
  --filter=':- .gitignore' \
  "$LOCAL_ROOT/" \
  "rw-build-server:$REMOTE_ROOT/"
```

- `--delete` keeps remote in sync with local (removes files deleted locally).
- `--exclude=.git/` is explicit because `.git/` is not in `.gitignore` (git never tracks itself, but rsync has no such knowledge).
- `--filter=':- .gitignore'` reads every `.gitignore` in the tree (same per-directory semantics as git), automatically excluding `target/`, `node_modules/`, `tmp/`, and everything else gitignored. This is self-maintaining — no manual exclude list to keep in sync.

### 3.3 Remote Execution

For `cargo xtask` subcommands, dispatch is automatic — `try_run_remote()` in `xtask/src/boundary/remote.rs` handles probe, sync, git init, and execution transparently. Developers just run `cargo xtask verify` and it runs on the remote.

For non-xtask commands, use `scripts/remote/run.sh`:

```bash
ssh -t rw-build-server "cd $REMOTE_ROOT && $COMMAND"
```

Exit code is forwarded as-is. stderr/stdout stream to the local terminal in real time via pseudo-TTY.

### 3.4 Artifact Copy-Back (for `cargo build` / `cargo test`)

For `cargo xtask verify`, no artifact copy-back is needed — the goal is just verification output.

For `cargo build` (producing a local binary) or `cargo test --no-run` (producing test executables), copy back the relevant artifact after the remote build:

```bash
rsync -az \
  "rw-build-server:$REMOTE_ROOT/target/debug/$BINARY" \
  "$LOCAL_ROOT/target/debug/$BINARY"
```

Note: Linux binaries cannot run on macOS. Artifact copy-back is only useful if the developer is also on Linux, or if the goal is CI artifact inspection. On macOS, the primary value of remote `cargo build` is compile-time error checking, not running the binary.

### 3.5 Remote Git Repository Initialization

The synced directory has no `.git/` (excluded from rsync). Tests that call libgit2 or `git rev-parse` need a valid git repo. Both the Rust implementation (`ensure_remote_git_repo` in `remote.rs`) and the shell wrapper (`run.sh`) initialize a minimal repo after sync:

```bash
cd $REMOTE_ROOT &&
  git rev-parse --git-dir >/dev/null 2>&1 ||
  (git init -q && git config user.email build@remote && git config user.name Build);
  git add -A -q 2>/dev/null;
  git commit -q --allow-empty -m sync 2>/dev/null || true
```

This is idempotent — it only runs `git init` if no repo exists, and the `git add + commit` ensures HEAD is valid for `git_diff` / `git_snapshot` operations.

### 3.6 Fallback Behavior

When the remote probe fails:
1. Print a single warning: `[remote-build] rw-build-server unreachable, running locally`
2. Execute the command locally as if no remote flag was given.
3. Exit with the local command's exit code.

No retry loop — if the server is down, local is the right answer immediately.

---

## 4) Implementation: Shell Scripts

All scripts live in `scripts/remote/`. They are thin wrappers; the actual build logic stays in `cargo xtask`.

### 4.1 `scripts/remote/probe.sh`

```bash
#!/usr/bin/env bash
# Exit 0 if rw-build-server is reachable, non-zero otherwise.
ssh -o ConnectTimeout=5 -o BatchMode=yes rw-build-server exit 0 2>/dev/null
```

### 4.2 `scripts/remote/sync.sh`

Rsyncs the working tree. Computes the same hash-based remote path as `run.sh` if `REMOTE_ROOT` is not set.

### 4.3 `scripts/remote/run.sh`

The main entry point for non-xtask commands. Accepts any command as positional arguments. Computes `REMOTE_ROOT` as `/tmp/rw-<sha256_16(git-root + hostname)>` — the same hash as the Rust implementation, so both paths share the same `target/` cache.

Usage:
```bash
# Non-xtask commands (xtask subcommands auto-dispatch — just run them directly):
./scripts/remote/run.sh cargo test -p ralph-workflow --lib
./scripts/remote/run.sh cargo test -p ralph-workflow-tests --test integration_tests
./scripts/remote/run.sh cargo build
./scripts/remote/run.sh cargo clippy -p ralph-workflow -- -D warnings
```

> **Note:** `cargo xtask verify`, `cargo xtask dylint`, etc. do not need `run.sh` — they auto-dispatch via the Rust `try_run_remote()`. The shell scripts are for arbitrary `cargo` commands.

### 4.4 Shell Aliases (optional, in local `~/.zshrc` or `~/.bashrc`)

```bash
alias rtest='./scripts/remote/run.sh cargo test'
alias rbuild='./scripts/remote/run.sh cargo build'
```

---

## 5) Remote Machine Setup (One-Time)

These steps are performed once on `rw-build-server`. They are not part of the repo automation.

```bash
# Rust stable + nightly
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env
rustup toolchain install nightly
rustup component add rustfmt clippy

# System dependencies
sudo apt-get update
sudo apt-get install -y \
  build-essential pkg-config cmake \
  libssl-dev libgit2-dev \
  make

# bun (frontend lane)
curl -fsSL https://bun.sh/install | bash

# dylint (pinned to 3.5.1 to match local)
cargo install cargo-dylint dylint-link --version 3.5.1

# mold linker (3-10x faster linking on Linux)
# Configured automatically via .cargo/config.toml [target.x86_64-unknown-linux-gnu]
sudo apt-get install -y mold clang

# sccache (cache across clean target/ wipes)
# Chained automatically via .cargo/rustc-wrapper-dylint when available on PATH
cargo install sccache --locked

# No need to create a working directory — the remote path is computed
# automatically as /tmp/rw-<hash> and created by rsync on first sync.
```

### PATH in non-interactive SSH sessions

SSH non-interactive sessions often have a stripped PATH. Ensure `~/.bashrc` on the remote exports the full PATH, and that it is sourced for non-interactive sessions by adding this near the top of `~/.bashrc`:

```bash
# Source for non-interactive SSH sessions
case $- in
  *i*) ;;
  *) source ~/.cargo/env; export PATH="$HOME/.bun/bin:$PATH" ;;
esac
```

---

## 6) Decision Flowchart

```
Developer runs a build/verify command
         │
         ▼
  Probe rw-build-server (5s timeout)
         │
    ┌────┴────┐
 reachable  unreachable
    │            │
    ▼            ▼
  rsync      run locally
  to remote  (fallback)
    │
    ▼
  SSH exec command on remote
  (streams output to local terminal)
    │
    ▼
  Exit with remote exit code
```

---

## 7) Out of Scope

- **Cross-compilation:** Remote is Linux; producing macOS binaries remotely is not planned.
- **Result caching / artifact registry:** sccache addresses compile caching; distributing final artifacts is not needed for this use case.
- **Multiple remote machines / load balancing:** Single `rw-build-server` is sufficient.
- **CI integration:** The remote build server is a developer productivity tool, not a replacement for CI. CI continues to run `cargo xtask verify` independently.
- **Automated remote provisioning (Ansible/Terraform):** Out of scope; one-time manual setup is documented in §5.

---

## 8) Acceptance Criteria

- [x] `cargo xtask verify` auto-dispatches to remote when `rw-build-server` is reachable (all 11 checks pass).
- [x] `scripts/remote/run.sh cargo test -p ralph-workflow --lib` syncs and runs on remote.
- [x] When `rw-build-server` is unreachable, falls back to local with a single warning line.
- [x] Probe adds ≤ 5 seconds to fallback path.
- [x] Exit codes from remote commands propagate correctly to the local shell.
- [x] Second sync after no file changes completes in under 3 seconds (rsync delta).
- [x] `ensure_remote_git_repo` initializes a valid git repo for libgit2-dependent tests.
