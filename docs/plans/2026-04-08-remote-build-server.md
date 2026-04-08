# Remote Build Server Plan

**Date:** 2026-04-08
**Status:** Proposed
**Scope:** Offload `cargo xtask verify`, `cargo build`, and `cargo test` to a dedicated remote build machine (`rw-build-server`), with automatic fallback to local execution when the remote is unreachable.

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
  --exclude 'target/' \
  --exclude '.git/' \
  --exclude 'ralph-gui/ui/node_modules/' \
  --exclude 'tmp/' \
  "$LOCAL_ROOT/" \
  "rw-build-server:$REMOTE_ROOT/"
```

- `--delete` keeps remote in sync with local (removes files deleted locally).
- `target/` is excluded — it lives on the remote persistently and is the main source of warm-cache speedup.
- `.git/` is excluded — git ops are local-only per the project's Ralph pipeline contract.

### 3.3 Remote Execution

After sync, run the command on the remote via SSH with a pseudo-TTY so output streams correctly:

```bash
ssh -t rw-build-server "cd $REMOTE_ROOT && $COMMAND"
```

Exit code is forwarded as-is. stderr/stdout stream to the local terminal in real time.

### 3.4 Artifact Copy-Back (for `cargo build` / `cargo test`)

For `cargo xtask verify`, no artifact copy-back is needed — the goal is just verification output.

For `cargo build` (producing a local binary) or `cargo test --no-run` (producing test executables), copy back the relevant artifact after the remote build:

```bash
rsync -az \
  "rw-build-server:$REMOTE_ROOT/target/debug/$BINARY" \
  "$LOCAL_ROOT/target/debug/$BINARY"
```

Note: Linux binaries cannot run on macOS. Artifact copy-back is only useful if the developer is also on Linux, or if the goal is CI artifact inspection. On macOS, the primary value of remote `cargo build` is compile-time error checking, not running the binary.

### 3.5 Fallback Behavior

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

```bash
#!/usr/bin/env bash
set -euo pipefail
LOCAL_ROOT="$(git rev-parse --show-toplevel)"
REMOTE_ROOT="${REMOTE_ROOT:-~/RalphWithReviewer}"

rsync -az --delete \
  --exclude 'target/' \
  --exclude '.git/' \
  --exclude 'ralph-gui/ui/node_modules/' \
  --exclude 'tmp/' \
  "$LOCAL_ROOT/" \
  "rw-build-server:$REMOTE_ROOT/"
```

### 4.3 `scripts/remote/run.sh`

The main entry point. Accepts any cargo/xtask command as arguments.

```bash
#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT="${REMOTE_ROOT:-~/RalphWithReviewer}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if "$SCRIPT_DIR/probe.sh"; then
  echo "[remote-build] syncing to rw-build-server..."
  "$SCRIPT_DIR/sync.sh"
  echo "[remote-build] running: $*"
  ssh -t rw-build-server "cd $REMOTE_ROOT && $*"
else
  echo "[remote-build] rw-build-server unreachable, running locally" >&2
  exec "$@"
fi
```

Usage:
```bash
# Full verify (primary use case)
./scripts/remote/run.sh cargo xtask verify

# Build only
./scripts/remote/run.sh cargo build

# Specific test
./scripts/remote/run.sh cargo test -p ralph-workflow --lib

# Integration tests
./scripts/remote/run.sh cargo test -p ralph-workflow-tests --test integration_tests
```

### 4.4 Shell Aliases (local `~/.zshrc` or `~/.bashrc`)

```bash
alias rverify='./scripts/remote/run.sh cargo xtask verify'
alias rbuild='./scripts/remote/run.sh cargo build'
alias rtest='./scripts/remote/run.sh cargo test'
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

# dylint
cargo install cargo-dylint dylint-link

# Optional: mold linker (significantly faster linking on Linux)
sudo apt-get install -y mold
# Add to ~/.cargo/config.toml on remote:
# [target.x86_64-unknown-linux-gnu]
# linker = "clang"
# rustflags = ["-C", "link-arg=-fuse-ld=mold"]

# Optional: sccache (cache across clean target/ wipes)
cargo install sccache
# Add to ~/.bashrc: export RUSTC_WRAPPER=sccache

# Create the working directory
mkdir -p ~/RalphWithReviewer
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

## 6) `cargo-remote` (Alternative for Cargo-Only Commands)

For developers who only need `cargo build` or `cargo test` (not the full verify pipeline), `cargo-remote` provides a transparent wrapper:

```bash
cargo install cargo-remote
```

Config in `~/.cargo/remote.toml`:
```toml
[remote]
host = "rw-build-server"
remote_path = "~/.cargo-remote/RalphWithReviewer"
```

Usage:
```bash
cargo remote build
cargo remote test -p ralph-workflow --lib
```

**Limitation:** `cargo remote` only forwards `cargo` subcommands. It cannot run `cargo xtask verify` end-to-end because verify also invokes `bun`, `make dylint`, and other non-cargo processes. Use `scripts/remote/run.sh` for full verify.

---

## 7) Decision Flowchart

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

## 8) Out of Scope

- **Cross-compilation:** Remote is Linux; producing macOS binaries remotely is not planned.
- **Result caching / artifact registry:** sccache addresses compile caching; distributing final artifacts is not needed for this use case.
- **Multiple remote machines / load balancing:** Single `rw-build-server` is sufficient.
- **CI integration:** The remote build server is a developer productivity tool, not a replacement for CI. CI continues to run `cargo xtask verify` independently.
- **Automated remote provisioning (Ansible/Terraform):** Out of scope; one-time manual setup is documented in §5.

---

## 9) Acceptance Criteria

- [ ] `scripts/remote/run.sh cargo xtask verify` syncs and runs on remote when `rw-build-server` is reachable.
- [ ] When `rw-build-server` is unreachable, the same command runs locally with a single warning line and no error.
- [ ] Probe adds ≤ 5 seconds to fallback path.
- [ ] `scripts/remote/run.sh cargo build` and `scripts/remote/run.sh cargo test` work as subsets.
- [ ] Exit codes from remote commands propagate correctly to the local shell.
- [ ] Second sync after no file changes completes in under 3 seconds (rsync delta).
