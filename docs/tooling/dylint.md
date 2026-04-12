# Custom Lints (dylint)

This repository uses [dylint](https://github.com/trailofbits/dylint) for custom Rust lints.

## Consolidation: All Lints in ralph_lints

**All custom lints have been consolidated into a single crate: `ralph_lints`.**

This was done for performance reasons:
- Building multiple separate lint crates (`--all`) significantly increases dylint driver build time
- Loading a single consolidated lint library reduces initialization overhead
- Faster iteration cycles during development

The individual lint crates (file_too_long, forbid_mut_binding, forbid_imperative_loops, forbid_mutating_receiver_methods, forbid_interior_mutability) have been removed. All lint implementations are now located in `lints/ralph_lints/src/`.

## Lint Severity Levels

The dylint lints are configured at varying severity levels in their definitions (e.g., `pub FORBID_MUT_BINDING, Warn, ...` or `pub FORBID_DOMAIN_BOUNDARY_DEPENDENCIES, Deny, ...`). Lint definitions use `Deny` for architectural rules that must never be bypassed, and `Warn` for rules where narrow heuristics approximate a property rather than fully proving it.

The build system passes `RUSTFLAGS="--cap-lints=deny -D warnings"` when running dylint:

- **`--cap-lints=deny`**: Caps `forbid`-level lints to `deny` so external crate lints don't cause unexpected build-system issues.
- **`-D warnings`**: Promotes **all warnings to errors**, including `Warn`-level custom lints. Every dylint violation — whether `Warn` or `Deny` — is a build failure.

This means there is no distinction between `Warn` and `Deny` severity in practice: both levels cause `cargo xtask verify` to fail. The severity labels in lint definitions document intent and expected frequency of violations, not whether the lint is enforced.

## Lint policy

The four functional-programming lints (`forbid_mut_binding`, `forbid_imperative_loops`,
`forbid_mutating_receiver_methods`, `forbid_interior_mutability`) each enforce a specific
functional programming principle.  The **rule itself** — what it forbids and where it
permits exceptions — **MUST NOT be altered**.  If the *implementation* has a bug (false
positives, false negatives, or code that contradicts the principle it enforces), fix the
implementation.  The spirit of the rule is authoritative, not the current code.

## FP principles behind the lints

The four functional lints are grounded in established functional programming practice,
particularly lessons from Haskell:

| Lint | FP principle | Haskell analogy |
|------|-------------|-----------------|
| `forbid_mut_binding` | **Immutability by default.** All bindings are immutable; values are transformed by producing new values. | Haskell has no `let mut` — every binding is immutable. |
| `forbid_imperative_loops` | **Avoid explicit recursion and imperative iteration.** Prefer higher-order combinators (`map`, `filter`, `fold`). | HaskellWiki: "Avoid explicit recursion — prefer `map`, `filter`, `foldr`." |
| `forbid_mutating_receiver_methods` | **Referential transparency and value semantics.** Data structures are persistent — operations return new values, not mutate in place. | `Data.Map.insert` returns a new map. |
| `forbid_interior_mutability` | **`&T` must mean truly immutable.** No mechanism to mutate behind a shared reference in pure code. | Haskell values are immutable; `IORef`/`MVar` exist only in `IO`. |

For practical examples of how to rewrite imperative code to satisfy these lints, see
`docs/code-style/functional-transformations.md`.

## Available Lints

| Lint | Severity | Description |
|------|----------|-------------|
| `file_too_long` | Deny | Rejects source files at 1000+ lines; 500+ lines remain a style-review guideline rather than a build-stopping lint |
| `forbid_mut_binding` | Warn | Rejects mutable bindings (`let mut`, mutable function parameters) outside boundary modules. **v1 scope:** pattern-based heuristic; does not prove the binding never escapes. |
| `forbid_imperative_loops` | Warn | Rejects `while`, `loop`, and `for` loop constructs outside boundary modules. **v1 scope:** pattern-based heuristic; does not prove the loop never performs effects. |
| `forbid_mutating_receiver_methods` | Warn | Rejects calls to `&mut self` methods unless the receiver type is an inherently-effectful I/O type or the call site is in a boundary module. **v1 scope:** type-based heuristic; does not prove the method has no side effects. |
| `forbid_interior_mutability` | Warn | Rejects interior-mutability types (`Cell`, `RefCell`, `Mutex`, `RwLock`, etc.) outside boundary modules. **v1 scope:** type-based heuristic; does not prove interior mutation never occurs. |
| `forbid_terminal_output` | Warn | Rejects direct terminal output (`println!`, `eprintln!`, etc.) outside boundary modules. **v1 scope:** pattern-based heuristic; does not prove no output occurs. |
| `forbid_io_effects` | Warn | Rejects direct effect access (`std::fs`, `std::env`, `std::process`, network, threads/tasks, randomness, stdio, clock reads) outside boundary modules. **v1 scope:** path-based heuristic; does not prove all effect paths are caught. |
| `forbid_nested_boundary_modules` | Deny | Rejects nested modules inside boundary directories so effect seams stay flat and wiring-focused |
| `boundary_function_too_complex` | Warn | Flags boundary functions exceeding a complexity threshold |
| `forbid_domain_boundary_dependencies` | Deny | Rejects `use` / `import` items that reference boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`, etc.) from non-boundary modules. Prevents domain code from directly depending on boundary implementations. **v1 scope:** path-based import matching only; does not trace re-exports. |
| `forbid_boundary_policy_calls` | Deny | Rejects direct calls from boundary modules to reducer/orchestrator policy helpers. Policy decisions belong in domain code. **v1 scope:** matches `reducer::determine_*`, `reducer::reduce_*`, `orchestrator::determine_*`, `orchestrator::reduce_*` call paths only; does not track indirect calls. |
| `forbid_result_swallowing` | Deny | Rejects silent Result discard patterns (`let _ = result`, `.ok()` on Result, single-arm `if let Err(_)` with unit body). Hidden failure handling undermines the explicit-effect model. **v1 scope:** does not detect match arms that explicitly handle both variants. |
| `forbid_raw_effect_types_in_public_apis` | Warn | Rejects public functions that return raw effect-native types (`std::process::Output`, `std::process::Child`) without translation. Boundary adapters should translate before returning inward. **v1 scope:** string-based type matching on function signatures; does not follow type aliases or trait bounds. |
| `forbid_boundary_retry_loops` | Deny | Rejects inline retry loops inside boundary modules that both perform I/O and track attempt counters (effect call + counter variable + increment + max check). Retry policy belongs in orchestration, not inline boundary code. **v1 scope:** pattern-based heuristic; does not prove termination or correctness. |

### Boundary modules

The lints `forbid_mut_binding`, `forbid_imperative_loops`, `forbid_mutating_receiver_methods`,
`forbid_interior_mutability`, `forbid_terminal_output`, `forbid_io_effects`, and all the
**forbid_boundary_*** and **forbid_raw_*** lints share the **boundary module** concept. Code in a
directory whose path contains one of these components is treated as an effect boundary.

Canonical architectural boundary categories:

- `io/`
- `runtime/`
- `ffi/`
- `boundary/`
- `executor/`

The lint crate also recognizes a small set of implementation-specific boundary markers used by
existing adapter code:

- `claude/`
- `codex/`
- `gemini/`
- `opencode/`
- `streaming_state/`
- `health/`
- `deduplication/`
- `delta_display/`
- `printer/`
- `mcp_server/`
- `harness/`
- `main` — binary entry point (`main.rs` files); subjects them to `boundary_function_too_complex`
  and `forbid_boundary_policy_calls` but exempts them from functional purity lints
  (`forbid_mut_binding`, `forbid_imperative_loops`, etc.) because `main` is inherently effectful:
  it reads process arguments, accesses the clock, and dispatches to real effects.

This mirrors the Haskell separation between pure computation and the `IO` monad, but with an
important repository-specific rule: a boundary marker is an effect seam, not a general escape
hatch. Mutation is tolerated there only because the underlying capability demands it.

Boundary modules are expected to:

- gather inputs from capabilities
- call pure helpers on ordinary values
- execute the requested effect
- translate effect failures or raw outputs into typed results and descriptive events

Boundary modules must not become a second policy engine. In particular they should not own:

- retry or fallback policy
- workflow progression decisions
- reducer/orchestrator decisions hidden behind effectful helpers
- business branching that should live in pure domain logic

`boundary_function_too_complex`, `forbid_nested_boundary_modules`, `forbid_boundary_policy_calls`,
and `forbid_boundary_retry_loops` exist specifically to keep those seams thin and visible.

When adding a new boundary marker to `BOUNDARY_MODULES`, the bar is high: the module's primary
purpose must be executing real effects (filesystem, env, process, network, stdio, time, threads,
randomness, or FFI). Local mutation alone is not enough.

Current `boundary_function_too_complex` thresholds:

- line threshold: 12+
- decision threshold: 2+
- complexity score threshold: 6+

The score also grows with statement count, boolean guard operators, match-arm count, and nesting.
The intent is to catch boundary functions that are no longer just wiring.

### Autogenerated file exemption

Autogenerated Rust files may opt out of `file_too_long` by placing `reason = "autogenerated"`
near the top of the file. When `cargo xtask verify` sees that marker, it prints
`[file] has been marked as autogenerated` as informational output.

### File length policy

`docs/code-style/module-organization.md` treats line count as a signal, not a goal in itself. This repository therefore uses `file_too_long` only for the hard-fail case at 1000+ lines, where a file is overwhelmingly likely to own too many responsibilities. The 500-line threshold remains a review guideline for contributors, not a deny-level lint.

## Running Lints

```bash
# Run all custom lints (via make - recommended)
make dylint

# Equivalent runner entrypoint
cargo xtask dylint
```

`cargo xtask dylint` resolves workspace packages from `cargo metadata`, lints each package with
`ralph_lints`, excludes lint crates themselves (for example `*_lints`), and keeps
`ralph-workflow` scoped to `--lib` to avoid known binary-target warning escalation.

## Local Crate Verification

To verify the lint crate itself compiles and passes its own unit tests without invoking the full
dylint driver UI test harness (which has a separate upstream toolchain issue), use these commands
from the `lints/ralph_lints` directory:

```bash
# Check the lint crate compiles cleanly
cd lints/ralph_lints && export RUSTUP_TOOLCHAIN="$(rustup show active-toolchain | cut -d' ' -f1)" && cargo check

# Run crate-local unit tests only (no dylint driver UI test)
cd lints/ralph_lints && export RUSTUP_TOOLCHAIN="$(rustup show active-toolchain | cut -d' ' -f1)" && cargo test --lib
```

**Note:** After the crate-local tests pass, the full `cargo test` run (which includes the
`dylint_testing::ui_test` in `lib.rs`) will still encounter a known upstream `dylint_driver` /
`dylint_testing` UI/toolchain issue. This failure is in the dylint harness itself, not in the
crate-local lint logic. Distinguish between "crate-local unit test failures" (real bugs) and
"dylint driver UI test failures" (known upstream issue).

## Rust LSP Integration

This repository includes a wrapper at `.cargo/rust-analyzer-dylint` for Rust LSP clients that use
`rust-analyzer.check.overrideCommand`.

- The wrapper runs `cargo clippy` first so standard Rust and clippy warnings surface in the editor.
- The wrapper then runs `cargo dylint` with JSON diagnostics enabled so custom lint diagnostics surface too.
- The wrapper finally runs `cargo xtask lsp-forbidden-allow-expect` so the native forbidden `#[allow(...)]` / `#[expect(...)]` audit also appears in the editor as JSON diagnostics.
- It filters non-JSON progress lines to stderr so rust-analyzer sees clean JSON on stdout.
- VS Code is preconfigured through `.vscode/settings.json`.
- Claude Code exposes the shared wrapper path through `.claude/settings.json` so the same command is available in project settings.

Use this command in clients that expose rust-analyzer settings, including VS Code and Claude Code:

```json
{
  "rust-analyzer.check.overrideCommand": [
    ".cargo/rust-analyzer-dylint"
  ]
}
```

OpenCode v1.2.27 rejects a top-level `lsp` key in `opencode.json`, so this repository does not check in an OpenCode project config for the wrapper.

## Developing Lints

Custom lints are consolidated in `lints/ralph_lints/`. Each lint module is located in `lints/ralph_lints/src/`.

To build and test lints:

```bash
cd lints/ralph_lints
cargo +nightly test
```

**Note:** Dylint lints require nightly Rust due to use of rustc internals.

## Environment Variables for Sandboxed Environments

The `make dylint` target respects standard Rust environment variables:

| Variable | Purpose | Example |
|----------|---------|---------|
| `CARGO_HOME` | Override cargo cache/bin location | `/tmp/cargo-cache` |
| `RUSTUP_HOME` | Override rustup installation location | `/tmp/rustup-home` |
| `DYLINT_DRIVER_PATH` | Override dylint driver cache location | `/tmp/dylint-drivers` |

For hermetic builds or CI environments with restricted HOME:

```bash
# Example: Run dylint in a sandboxed environment
export CARGO_HOME=/writable/path/cargo
export RUSTUP_HOME=/writable/path/rustup
export DYLINT_DRIVER_PATH=/writable/path/drivers
make dylint
```

When `CARGO_HOME` points to a writable temp directory in a sandboxed environment, `make dylint`
will reuse the existing `~/.cargo/registry/cache` and `~/.cargo/registry/index` data when
available and automatically switch Cargo to offline mode. This avoids unnecessary network access
while still allowing the lint crate under `lints/ralph_lints` to build.

## Known Issues

### dylint_driver build failure (v3.5.1 and later)

If you encounter an error like:

```
error: environment variable `RUSTUP_TOOLCHAIN` not defined at compile time
```

This is a known upstream bug in dylint_driver (v3.5.1, v5.0.0, and potentially other versions) that occurs when cargo-dylint rebuilds the driver. The driver build script requires the `RUSTUP_TOOLCHAIN` environment variable to be set at compile time using `env!()`, but cargo-dylint explicitly unsets it when spawning the driver build subprocess (`env -u RUSTUP_TOOLCHAIN cargo build`).

### Solution implemented in `make dylint`

The `make dylint` target implements a multi-layered approach intended to ensure the dylint driver is built with the nightly toolchain:

1. **Environment validation:** Checks that CARGO_HOME, RUSTUP_HOME, and DYLINT_DRIVER_PATH are writable
2. **Toolchain bootstrapping:** Installs rustup (if missing) and nightly toolchain with required components (rustc-dev, llvm-tools-preview)
3. **Toolchain discovery:** Dynamically discovers the installed nightly toolchain name (e.g., `nightly-aarch64-apple-darwin`) to support specific nightly versions
4. **Cargo wrapper script:** Creates a temporary wrapper script that exports the discovered nightly toolchain before exec'ing the real nightly cargo
5. **PATH manipulation:** Prepends the wrapper directory and nightly bin directory to PATH, ensuring the wrapper is found first
6. **Environment export:** Exports RUSTUP_TOOLCHAIN, RUSTC, and all cache location variables

### How the wrapper works

When cargo-dylint runs `env -u RUSTUP_TOOLCHAIN cargo build` to rebuild the driver, it:

1. Unsets RUSTUP_TOOLCHAIN in the subprocess environment
2. Searches PATH for the `cargo` binary
3. Finds and executes the wrapper script (first in PATH)
4. Wrapper exports RUSTUP_TOOLCHAIN with the dynamically discovered nightly toolchain name
5. Wrapper execs the real nightly cargo with RUSTUP_TOOLCHAIN set

This approach works around cargo-dylint's explicit unsetting of RUSTUP_TOOLCHAIN, addressing the E0554 failure mode where cargo-dylint rebuilds its driver using a stable toolchain.

### Limitations

This Makefile fix cannot fully eliminate upstream failures where cargo-dylint (or the driver build) requires additional environment variables or pre-provisioned components in strictly offline/sandboxed environments.

## Troubleshooting `make dylint`

### Symptom: E0554 error during dylint driver build

```
error[E0554]: `#![feature]` may not be used on the stable release channel
```

**Cause:** Driver build used stable cargo instead of nightly

**Solution:** Verify nightly toolchain is installed with required components:

```bash
rustup toolchain install nightly --profile minimal
rustup component add rustc-dev llvm-tools-preview --toolchain nightly
```

If the issue persists, use the verbose mode to debug PATH resolution:

```bash
make dylint-verbose
```

---

### Symptom: "cannot create required directory" error

```
error: cannot create required directory: /path/to/dir
```

**Cause:** HOME or cache directories are not writable

**Solution:** Set writable locations explicitly:

```bash
export CARGO_HOME=/tmp/cargo
export RUSTUP_HOME=/tmp/rustup
export DYLINT_DRIVER_PATH=/tmp/drivers
make dylint
```

---

### Symptom: Network errors during toolchain/component installation

```
error: failed to install nightly toolchain
```

**Cause:** Offline environment cannot fetch toolchains

**Solution:** Pre-install nightly with components before running make dylint:

```bash
# In an online environment, install required toolchain and components
rustup toolchain install nightly --profile minimal
rustup component add rustc-dev llvm-tools-preview --toolchain nightly

# Install cargo-dylint globally
cargo install cargo-dylint dylint-link

# Now `make dylint` will work offline
```

---

### Symptom: "dylint-driver" not found or not functional

```
Warning: command failed: "~/.dylint_drivers/nightly-*/dylint-driver" "-V"
```

**Cause:** Corrupted or mismatched dylint driver cache

**Solution:** Clean the driver cache and rebuild:

```bash
rm -rf ~/.dylint_drivers
make dylint
```

---

### Symptom: Warning about cargo not resolving to wrapper

```
warning: cargo resolves to /usr/local/bin/cargo instead of /tmp/xyz/cargo
Continuing anyway, but this may cause issues...
```

**Cause:** System PATH configuration or shell aliases override the wrapper

**Solution:** Check for shell aliases or functions that override cargo:

```bash
# Check for cargo alias or function
type cargo

# If an alias exists, unalias it temporarily
unalias cargo

# Run make dylint again
make dylint
```

---

### Debugging with dylint-verbose

To see detailed information about PATH, cargo resolution, and toolchain selection:

```bash
make dylint-verbose
```

This will display:

- CARGO_HOME, RUSTUP_HOME, DYLINT_DRIVER_PATH locations
- PATH resolution (first 3 entries)
- Wrapper script path and contents
- Which cargo binary is being used (via `command -v` and `which`)
- RUSTUP_TOOLCHAIN, RUSTC, and CARGO environment variables
- Nightly toolchain bin directory location

Use this output to diagnose PATH resolution issues or verify the nightly toolchain is correctly configured.
