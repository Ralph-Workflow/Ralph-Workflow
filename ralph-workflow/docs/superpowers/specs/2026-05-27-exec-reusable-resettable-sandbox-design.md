# Exec Reusable Resettable Sandbox Design

## Goal

Replace per-invocation exec overlays with a reusable, resettable sandbox pool that preserves Ralph's current isolation guarantees while materially improving performance, supporting same-workspace parallel exec, and bounding disk usage for one of the most frequently used agent commands.

## Scope

In scope:
- `ralph.mcp.tools.exec` sandbox lifecycle
- reusable per-workspace sandbox-pool design
- portable reset contract with optional internal accelerators
- isolated git behavior inside the sandbox
- concurrency control for repeated and overlapping exec usage
- stale sandbox recovery and orphan process cleanup
- TDD-first implementation strategy and regression coverage

Out of scope:
- weakening exec isolation guarantees
- changing the public exec tool API
- platform-specific behavior differences visible to users or agents
- unrelated MCP tool performance work

## Problem Statement

The current exec path creates a fresh overlay directory for each invocation. That has two costs:

1. **Performance cost:** every exec rebuilds workspace state even though exec is a hot path for agents.
2. **Space cost:** abandoned overlay directories can accumulate, and per-run directories create churn even when cleanup succeeds.

Recent hardening already reduced `.git` duplication and prunes dead-owner overlay directories, but the dominant model is still per-run overlay creation. The next step is architectural: reuse sandbox slots inside a workspace-keyed pool while fully resetting each leased slot so repeated and overlapping exec calls remain isolated without paying full setup costs every time.

## Design Requirements

The new design must preserve the existing observable guarantees proven by current tests:

- writes during exec never mutate the real workspace
- git operations inside exec do not corrupt or rewrite the real repository state
- rewritten environment paths point into the sandbox, not the source workspace
- generated directories remain excluded from the mirrored execution surface
- descendant/orphan processes are terminated when exec completes or is reclaimed
- behavior is uniform across macOS, Linux, and Windows

Additional requirements:

- one workspace may be executed repeatedly without unbounded cache growth
- concurrent exec attempts for the same workspace must behave deterministically
- implementation must follow strict TDD: failing tests first, then minimal code, then refactor
- exec should be optimized as a hot path for agent usage, not merely made correct under reuse

## Architecture

### 1. Workspace-scoped reusable sandbox pool

Each source workspace gets a reusable sandbox pool under the private exec base. Ralph reuses pool slots, not the mutated contents left by prior runs.

Each slot has:
- a stable root path for the mirrored worktree
- a slot index within the workspace pool
- a lock file for exclusivity at the slot level
- owner metadata for crash recovery
- isolated git metadata separate from the source repository

The pool itself is keyed by a hash of the absolute workspace path so sibling worktrees and different projects cannot collide. Slot directories are indexed by both workspace hash and slot number.

### 2. Reset-before-run lifecycle

Every exec invocation must perform a reset cycle before spawning the subprocess:

1. resolve sandbox key from workspace root
2. choose or grow a pool slot for that workspace
3. acquire the slot lock
4. verify prior owner liveness and reclaim stale slots if needed
5. fully reset sandbox contents to a clean baseline
6. rebuild isolated git state
7. rewrite env paths for the leased slot
8. run subprocess
9. kill descendants/orphans, remove the leased worktree, release the slot lock, and update pool sizing state

Reuse is only at the slot level. A run never inherits filesystem mutations from a previous run, even when multiple slots exist for the same workspace.

### 3. Portable behavior, optional internal acceleration

The reset contract must be expressed in portable logic so behavior is identical on all supported platforms. Internal acceleration is allowed only if the post-reset sandbox is semantically identical.

Examples of acceptable invisible accelerators:
- faster copy primitives
- clone/hardlink-like strategies where safe
- optimized git metadata reconstruction

These must remain implementation details. The observable contract cannot differ by platform.

### 4. Optimization-first execution strategy

The reusable sandbox is not just a cleanup improvement; it is a hot-path optimization project. The design should explicitly optimize for:

- minimizing repeated filesystem churn across back-to-back exec calls
- minimizing repeated git metadata setup work when the source workspace has not materially changed
- bounding disk usage to stable per-workspace sandbox pools rather than per-run directories
- keeping reset costs proportional to the actual changed surface where correctness can still be proven

The implementation should therefore be staged:

1. establish the reusable sandbox contract and correctness coverage
2. make the reusable path the default for repeated exec on the same workspace
3. only then optimize the reset internals, but only behind tests that prove semantic equivalence

## Reset Algorithm

### Baseline reset path

The portable reset algorithm should:

1. enumerate and remove current sandbox worktree contents
2. repopulate the sandbox from the real workspace
3. exclude generated directories exactly as current exec does
4. recreate the isolated private gitdir backed by shared source objects where supported by current design
5. validate expected sentinel structure before execution begins

This is intentionally a full reset of the sandbox worktree, not an incremental cleanup of guessed dirty files. Incremental cleanup is faster in theory but too fragile for a high-frequency command that agents rely on heavily.

However, the design should leave room for a later **validated fast reset** path, where Ralph can skip unnecessary rebuild work only if it can prove the resulting sandbox is identical to a clean rebuild. Examples include:

- reusing stable sandbox root metadata while replacing only worktree contents
- short-circuiting git metadata reconstruction when the source git state is unchanged and sandbox validation proves the private gitdir remains equivalent
- using a manifest or comparable proof mechanism to avoid recopying unchanged files, but only if black-box tests prove equivalence with a full reset

The key rule is: optimization may reduce work, but never reduce proof.

### Rebuild fallback

If reset validation fails, Ralph must discard the leased slot state entirely and rebuild it from the real workspace before starting the subprocess.

That keeps the fast path aggressive without trusting a potentially corrupted sandbox.

## Concurrency Model

Concurrency must be a first-class design concern.

### Same-workspace concurrency

For the same workspace key, exec must allow overlapping runs without letting two subprocesses mutate the same leased filesystem. The design therefore uses a **dynamic sandbox pool**:

- one active exec per leased slot
- later callers first try an existing free slot for that workspace
- when all current slots are busy, Ralph can grow the pool by creating another slot
- after demand drops, Ralph can shrink idle extra slots back down

The sizing policy should be adaptive, not hard-coded per project. Ralph persists a small learned target slot count per workspace so repeated contention can be absorbed without rediscovering the same concurrency level every run.

### Cross-workspace concurrency

Different workspaces must use different workspace-path hashes and therefore run concurrently without interference.

### Crash recovery

Owner metadata plus lock recovery must handle:
- prior process exited cleanly
- prior process crashed
- prior process left descendants behind
- prior process disappeared but stale metadata remained

Recovery must happen before reset begins.

## Git Isolation

The sandbox continues to provide isolated git behavior:

- no full `.git` copy into the sandbox worktree
- sandbox `.git` points at private git metadata
- private metadata uses shared source objects where current design already proves this safe

This preserves current black-box git guarantees while minimizing setup cost and space consumption.

## Failure Handling

The system must fail safely under partial reset or concurrency conflicts.

Required behavior:
- if slot acquisition fails after the configured wait window, exit deterministically
- if reset fails, do not run the subprocess in a partially reset sandbox
- if rebuild fallback fails, surface a typed execution failure
- always run process-tree cleanup after execution attempts and during stale-owner reclamation

Performance failures should also degrade safely. If an optimized reset path cannot prove validity quickly, Ralph must fall back to the slower known-correct reset path rather than guessing.

## Testability Requirements

Implementation must be TDD-first and black-box oriented.

### Required failing tests before implementation

- sandbox is reused for repeated exec calls on the same workspace
- second exec sees a clean filesystem despite prior sandbox mutations
- git commands remain isolated across reused runs
- env rewriting still points into sandbox paths after reuse
- stale/crashed sandbox is reclaimed safely
- same-workspace concurrent exec attempts lease distinct slots or fail deterministically only when slot acquisition itself cannot succeed safely
- different workspaces can execute concurrently without interference
- reset corruption triggers rebuild fallback and still succeeds cleanly
- repeated exec on the same workspace demonstrates measurably lower setup churn than per-run disposable overlays through observable seams appropriate for testing

### Test style requirements

- prefer observable behavior over internal state assertions
- use injected seams/fakes where needed to keep tests fast
- add focused subprocess E2E coverage only where black-box proof requires it
- preserve and extend current exec regression coverage instead of replacing it

## Acceptance Criteria

- repeated exec calls on the same workspace reuse stable sandbox slot paths rather than always creating fresh overlays
- each exec run still observes a clean sandbox filesystem baseline
- the real workspace remains untouched by exec writes and git operations
- same-workspace concurrent exec usage is deterministic, race-safe, and can run in parallel by leasing separate slots
- cross-workspace exec usage remains concurrently safe
- stale sandbox directories no longer grow without bound
- behavior remains uniform across supported platforms
- new functionality is implemented through TDD with failing tests first
- the reusable pool path is the default hot path for repeated same-workspace exec usage
- `make verify` passes

## Risks and Mitigations

- **Risk:** Reuse leaks stale state between runs.
  - **Mitigation:** Full reset-before-run, reset validation, rebuild fallback, black-box reuse tests.

- **Risk:** Pool growth or shrink heuristics oscillate and create avoidable churn.
  - **Mitigation:** Persist a learned target slot count, decay only after repeated low-pressure runs, and cover both growth and shrink behavior with focused tests.

- **Risk:** Platform-specific acceleration changes semantics.
  - **Mitigation:** Keep accelerators behind the same reset contract and require identical black-box outcomes across platforms.

- **Risk:** Optimization effort chases micro-speed while missing hot-path correctness.
  - **Mitigation:** Prioritize reusable-slot architecture first, then optimize internal reset mechanics only after correctness coverage exists.

- **Risk:** A future fast-reset optimization becomes an untested divergence from the clean rebuild path.
  - **Mitigation:** Require every optimization path to preserve the same black-box outcomes as the baseline reset path and to fall back automatically when proof is incomplete.

## Review Notes

- This design keeps the current exec contract intact while changing the lifecycle model from disposable overlays to reusable resettable sandbox pools.
- The design intentionally avoids incremental dirty-file cleanup because correctness matters more than theoretical best-case speed for agent-heavy usage.
- Concurrency is part of the core design, not deferred follow-up work, because exec is a shared hot path used by multiple agents.
