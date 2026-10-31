# Remote sync: the local target follows the remote

> **Page role:** explanation and reference for contributors who change
> auto-integration. It states what remote sync guarantees, which code paths
> pull from the remote, how the background ticker is scheduled, when the code
> refuses to move a ref, and which tests pin all of it. Operator-facing
> configuration lives in the manual's
> [Auto-integration section](../sphinx/configuration.md#auto-integration).

## The contract

Remote sync is on when `general.auto_integrate_remote_enabled` is true (the
default) and the remote named by `general.auto_integrate_remote` (default
`origin`) exists in the repository. With remote sync on:

1. Every automatic entry point fetches `<remote>/<target>` and, when the
   remote tip strictly descends the local target, **fast-forwards the local
   target to it**. A target checked out in a worktree moves through that
   worktree's `git merge --ff-only`, so its ref, index and files stay
   consistent. A target with no checkout moves by an atomic compare-and-swap.
   A checked-out target is never moved with a bare `update-ref`.
2. The feature branch is then integrated onto the moved target: the commit
   seam rebases and lands it, and the catch-up ticker fast-forwards a checkout
   that has no commits of its own.
3. Local target commits that the remote lacks are published, and a target
   that diverged from the remote is reconciled by rebasing it onto the remote
   tip. The local target is never force-moved and nothing is force-pushed.
4. Unsafe states fail closed and are recorded (see
   [Fail-closed cases](#fail-closed-cases)). A later seam or tick retries.

With remote sync **disabled** (`auto_integrate_remote_enabled = false`), or in
a repository without the configured remote, auto-integration is
**local-only**: remote state never moves a local ref or gates integration. The
read-only freshness probe still reports what it saw. The local-only rules
(target resolution from local refs only, an observe-only refresh, no remote
condition gating integration) apply only in that mode.

## Entry points that pull

| Entry point | Code | What it does with remote sync on |
|---|---|---|
| Post-commit integration | `auto_integrate.auto_integrate_after_commit` (called by `pipeline/runner.py` after `COMMIT_SUCCESS`) | The attempt loop's `freshen_target` runs `pull_and_reconcile_target` before every rebase attempt. When the checkout is on the target, or HEAD equals the local target, `_sync_remote_before_target_skip` first runs the phase-boundary freshness and publication pass instead of recording a stale skip. |
| Phase transition | `auto_integrate.auto_integrate_on_phase_transition` → `auto_integrate_boundary.phase_boundary_outcome` | On a clean tree: pull (`pull_and_reconcile_target`), then publication (`reconcile_remote_publication`). A dirty tree defers. |
| Startup recovery | `auto_integrate_recovery.recover_incomplete_integration` | Under the integration transaction, runs `reconcile_remote_publication` before record recovery. A remote that is ahead advances the target directly. |
| Periodic catch-up ticker | `auto_integrate_catchup.AutoIntegrateCatchupWorker` | Each tick advances the target from the remote (below), fast-forwards a behind-and-clean checkout onto it, then runs the publication reconciler. |

Every target advance goes through one routine:
`auto_integrate_remote_client.fast_forward_target_to_remote`. It refuses a
missing ref or a diverged target, and otherwise delegates to the landing
table in `auto_integrate_ff.fast_forward_target`.

### How the ticker is scheduled, throttled and leased

- **Scheduling.** `run_loop` calls `start_catchup_worker_if_enabled` once per
  run (when `auto_integrate_enabled` is true). It starts a daemon thread that
  ticks every `DEFAULT_CATCHUP_INTERVAL_SECONDS` (30 s); the first tick runs
  one interval after start. The run's cleanup step calls `stop()`.
- **Fetch throttle.** `auto_integrate_catchup_coordination.remote_sync_transaction`
  holds a non-blocking OS lock keyed by (common git dir, remote, target) and
  persists a `fetch_after` deadline under `<common-dir>/ralph/auto-integrate-catchup/`.
  All worktrees of one repository share it, so the fleet's ticker fetches at
  most once per `REMOTE_SYNC_INTERVAL_SECONDS` (300 s). Clock rollback and
  corrupt state are bounded to one interval.
- **Advance on every tick.** The throttle limits only the network fetch. On a
  throttled tick, `_cached_remote_strictly_ahead` compares the local target
  with the remote-tracking ref that some earlier fetch left (the post-tick
  publication and the commit seams refresh it continually) and advances when
  that is a strict fast-forward.
- **Leases.** If another worktree holds the remote-sync lock, the tick
  reports `remote-skipped`. If the repository mutation lease
  (`git_command_queue`) is held, the tick reports `refused: agent is currently
  active`. Each target advance is charged write-ahead to the durable attempt
  ledger under the signature `catchup-target|remote=…|target=…@<sha>`, and
  repeated failures against the same remote tip escalate through the
  quarantine ladder.
- **Branch independence.** The target sync runs whatever the run checkout is
  on, including a detached HEAD.

## Fail-closed cases

| State | Behaviour |
|---|---|
| Dirty checkout that owns the target (ticker) | `refused`; ref, index and files untouched; the next tick retries from the cached tracking ref once the owner is clean. |
| Dirty checkout that owns the target (post-commit or phase seam on that checkout) | The boundary defers; nothing moves. |
| Target owner refuses `merge --ff-only` (publication, startup, rejected push) | Reclamation is off on these paths. The result is recorded as `pull failed` with the real refusal reason and a deferred publication. |
| Target diverged from the remote | Never fast-forwarded or force-moved. The publication reconciler rebases the target onto the remote; a conflict with no resolver aborts cleanly and both refs stay put. |
| Remote unreachable | The ticker reports `remote-skipped` and makes no local change on that tick. Seams record a degraded base and integrate locally. |
| Cached tracking ref equal, missing, behind or diverged | No advance. |

The pull side of the commit and phase seams honours
`auto_integrate_reclaim_target_worktree` (default `true`). When `merge
--ff-only` refuses because of a dirty owner, those seams snapshot the owner to
`refs/ralph-reclaim/<target>/…` and reset it before retrying. Background
paths never reclaim.

## The 2026-10-30 bug

Reported symptom: with remote sync enabled, the automatic rebase did not
fast-forward local branches to origin, and the periodic task did not either.
The root causes, with pre-fix line numbers at `77b10ca38`:

1. **Post-commit skipped before pulling.** `auto_integrate._auto_integrate_check_skip_conditions`
   (`auto_integrate.py:740`, `:747`) recorded `on target branch` and `no
   commits beyond target` from the local pointer. `auto_integrate_after_commit`
   (`:337`) and the attempt loop (`_auto_integrate_attempt_loop.py:259`)
   returned those skips before `freshen_target` (`:315`) could fetch. A run
   on the target branch (trunk layout) or a feature at the local target never
   pulled or published after a commit.
2. **Ticker coupled fetch and advance.** `auto_integrate_catchup._synchronize_remote_target`
   returned early on a throttled lease (`auto_integrate_catchup.py:541`).
   Inside the five-minute cadence a known-ahead tracking ref was never
   applied. A refused advance (dirty owner) armed the cadence and was not
   retried for five minutes.
3. **Publication delegated to the whole tick.**
   `auto_integrate_remote_sync._dispatch_ref_comparison`
   (`auto_integrate_remote_sync.py:477-509`) and
   `auto_integrate_remote_push._align_target_with_remote`
   (`auto_integrate_remote_push.py:568-575`) called
   `attempt_catchup_fast_forward` and required the literal `fast-forwarded`.
   That tick is fetch-throttled. Inside the ticker's post-tick publication it
   re-acquired the mutation lease the worker already held
   (`auto_integrate_catchup.py:984` → `:383`), so it always refused itself.
   It also reported the checkout's catch-up, not the target's. The startup
   seam therefore reported `pull failed` with a false `origin unreachable`
   refresh even when the target had moved, and did not advance at all inside
   the cadence.
4. **Detached checkout stopped the tick.** `attempt_catchup_fast_forward`
   returned `not-on-branch` (`auto_integrate_catchup.py:318`) before the
   target sync.

## Regression tests

| Test | Pins |
|---|---|
| `tests/test_auto_integrate_remote_sync_fast_forward_contract.py` | Real-Git contract across post-commit, ticker and startup, in feature-worktree, trunk, detached, dirty, diverged, non-default-remote and remote-disabled topologies (root causes 1-4). |
| `tests/test_auto_integrate_remote_sync_ticker_cadence.py` | Fast in-process proofs: a throttled tick applies only a strictly-ahead tracking ref; publication advances the target itself and reports a refusal by its cause. |
| `tests/test_auto_integrate_catchup_e2e.py` | Real-Git ticker landing, clean and dirty target owners, cross-worktree lock. |
| `tests/test_auto_integrate_catchup_remote_coordination.py` | The 300 s fetch cadence and the shared persisted throttle. |
| `tests/test_auto_integrate_remote_refresh.py` | The read-only probe, divergence reconciliation, and the remote-disabled local-only contract. |
| `tests/test_auto_integrate_acceptance_scenarios_e2e.py` | Publication convergence against a real bare remote. |

Run them with:

```bash
uv run python -m pytest -q -p no:randomly -n 6 \
  tests/test_auto_integrate_remote_sync_fast_forward_contract.py \
  tests/test_auto_integrate_remote_sync_ticker_cadence.py \
  tests/test_auto_integrate_catchup_e2e.py \
  tests/test_auto_integrate_catchup_remote_coordination.py \
  tests/test_auto_integrate_remote_refresh.py
```

## Related

- [Verification](verification.md#remote-sync-the-target-follows-the-remote) —
  where this suite sits in the verification gate.
- [ADR-0007](../architecture/adr-0007-integration-source-of-truth.md) —
  integration and publication authorities.
- [Configuration: auto-integration](../sphinx/configuration.md#auto-integration) —
  operator keys and defaults.
