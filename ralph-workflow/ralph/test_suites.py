"""Run the maintained pytest verification suite under the current interpreter.

.. note::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget is enforced
    UPSTREAM by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking. This module additionally owns one parent
    deadline across all concurrent file shards. Splitting tests into shards
    does NOT increase either budget.
"""

from __future__ import annotations

import ast
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.executor.process import TIMEOUT_EXIT_CODE
from ralph.process._spawn_env import sanitize_process_environment
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    build_timeout_env,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from typing import Protocol

    class ShardProcess(Protocol):
        """Controllable pytest shard process."""

        def poll(self) -> int | None: ...

        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes | None, bytes | None]: ...

        def terminate(self, grace_period_s: float | None = None) -> None: ...

        def cleanup_orphans(self) -> None: ...

    class ShardSpawner(Protocol):
        """Spawn seam for one plain-pytest shard."""

        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str],
        ) -> ShardProcess: ...


# Each shard is one pytest process (in-shard xdist is opt-in, see
# ``_xdist_workers_per_shard``), so a shard's wall clock scales with the
# files it is given. The default profile uses one plain pytest process per
# shard. Operators may override ``PYTEST_WORKERS`` for smaller hosts.
_PYTEST_SHARD_PROCESS_MANAGER = ProcessManager(
    policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False)
)
_DEFAULT_PYTEST_WORKERS = "auto"
# Hard cap on the number of plain-pytest shards; raising this cap does NOT
# raise the combined 60-second budget tracked upstream in
# ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``. The 8-shard cap is the
# verified-only size that keeps the maintained 40-core host's full
# selected suite inside the immutable 60 s combined budget while
# preserving exact-once selection; any larger value exhausts the budget
# on the grown E2E set. The 60s combined budget tracked by
# ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` is preserved; this is
# a runner-architecture choice, not a budget-constant change.
_HETEROGENEOUS_CORE_HOST_MAX_CORES = 12


def _max_pytest_workers_for_host() -> int:
    return 8


_MAX_PYTEST_WORKERS = _max_pytest_workers_for_host()
# The maintained 12-core host completes the suite with 10 shards under
# _PERFORMANCE_CORE_PYTEST_WORKER_CAP; hosts with >12 cores are bounded by
# _MAX_PYTEST_WORKERS (8 shards) to avoid startup and filesystem contention.
_PERFORMANCE_CORE_PYTEST_WORKER_CAP = 10
_MINIMUM_MULTI_SHARD_CORES = 2
# Default in-shard xdist worker count is ``"0"`` (plain pytest per shard)
# because on the maintained 8-shard CI profile the shard-saturated
# 8-shard fan-out already uses one pytest process per shard and adding
# xdist workers inside each shard shifts wall-clock budget from
# parallel-IO back into pytest-coordination overhead. Operators may
# override with ``PYTEST_XDIST_WORKERS_PER_SHARD=auto`` for the legacy
# CPU-utilisation-maximising policy or an explicit integer for a custom
# in-shard fan-out.
_DEFAULT_XDIST_WORKERS_PER_SHARD = "0"
# Hard cap on the number of pytest-xdist workers spawned INSIDE each shard.
# Combined with ``_MAX_PYTEST_WORKERS`` (8 shards) this gives a maximum
# fan-out of 8 * 4 = 32 workers when ``_DEFAULT_XDIST_WORKERS_PER_SHARD``
# is overridden to ``"auto"`` or a positive integer. The default plain-
# pytest path keeps the slowest shard under the 60s combined budget on
# 8-shard CI without coordination overhead.
_MAX_XDIST_WORKERS_PER_SHARD = 4

#: Exact subprocess-E2E files required by the authoritative verification
#: profile. This registry also drives the focused Make target, so the two
#: selections cannot drift.
EXCLUSIVE_SUBPROCESS_E2E_FILES: tuple[str, ...] = (
    "tests/agents/test_terminal_state_restored_on_exit.py",
)

#: The real-git acceptance files the default ``make test`` profile MUST
#: charge. Every entry has three coupled roles, and a file must satisfy
#: all three to stay on the default gate:
#:
#: 1. **Selection** -- static discovery force-includes a registered file
#:    even when it carries a module-level ``subprocess_e2e`` marker
#:    (``_inspect_test_file``), and the registry entry is what
#:    ``tests/conftest.py`` matches to stamp every item with
#:    ``required_auto_integrate_e2e`` so the DEFAULT marker expression
#:    selects it.
#: 2. **Audit sanction** -- ``ralph.testing.audit_test_policy`` exempts a
#:    registered file's real-Git crossings, so the proof may drive the
#:    real git binary without an opt-in marker.
#: 3. **Budget charge** -- a registered file runs in the dedicated
#:    real-git lane, whose own shards absorb its wall clock inside the
#:    pinned 60 s combined budget.
#:
#: A file that is NOT registered and carries a module-level
#: ``subprocess_e2e`` marker is dropped by discovery and deselected by
#: the default marker expression: its coverage rots in the opt-in lane
#: while ``make verify`` stays green. Register it here instead.
REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    # One real-git landing journey retains the external Git boundary proof;
    # decision and recovery variants run in the opt-in subprocess profile.
    "tests/test_auto_integrate_end_to_end.py",
    # wt-03 WU-R1: detached-HEAD recovery uses the worktree-private reflog
    # to reconstruct the original branch. The fail-closed exact-tip invariant
    # must stay on the default ``make test`` profile so the
    # permissive-ancestor / fall-through-to-older-entry regression cannot rot.
    "tests/test_auto_integrate_recovery_git_state_reflog.py",
    # Every merge-refusal shape (untracked / uncommitted obstructions) must
    # end with the branch containing its target -- the guard against
    # planning on a stale branch. Real git, so it is registered here.
    "tests/test_integration_obstruction_real_git.py",
    "tests/test_pending_merge_commit_recovery.py",
    "tests/test_pending_merge_repair_real_git.py",
    "tests/test_retained_resolution_handoff.py",
    "tests/test_retained_resolution_dispatch.py",
    "tests/integration/test_retained_integration_acceptance.py",
    # AC5-AC9 real-Git acceptance proofs: throttle-suppressed publish,
    # killed-between-landing-and-push recovery, unreachable-then-reachable
    # auto-push, concurrent remote advance with real ref equality. These
    # scenarios were previously only mocked; the live git boundary proof
    # must stay on the default ``make test`` profile so the publication
    # reconciler cannot regress to fire-once semantics or fire-and-forget
    # ``origin``/``main`` defaults.
    # 2026-10-30 regression: with remote sync enabled, post-commit, startup and
    # the periodic catch-up ticker MUST fast-forward the local target to the
    # remote (docs/agents/remote-sync.md). Remote sync is the most important
    # feature, so its real-Git contract runs on every default ``make test``.
    "tests/test_auto_integrate_remote_sync_fast_forward_contract.py",
    "tests/test_auto_integrate_acceptance_scenarios_e2e.py",
    # Real-Git proof that auto-rebase is idempotent and that a run starting
    # inside a paused, conflicted rebase hands every stop to the resolver.
    "tests/test_auto_rebase_idempotence_e2e.py",
    # Real-Git regression for the workspace-bounded git cwd contract
    # (symlink and parent-repo bypass shapes). Must run under the default
    # ``make test`` profile so the boundary cannot rot silently.
    # U5: worktree-qualified backup-ref identity. Real-Git proof that
    # linked worktrees sharing a pre-feature SHA get distinct refs and
    # that one operation's cleanup never enumerates the namespace.
    "tests/test_backup_ref_operation_scope.py",
    "tests/test_tool_git_read_path_validation.py",
    # I1/I2/I4 housekeeping bounds live in a subprocess_e2e module; keep
    # them on the default make-test profile via this registry.
    "tests/test_commit_review_invariants.py",
    "tests/test_commit_review_verify_gate.py",
    "tests/integration/test_pipeline_commit_review_to_commit_e2e.py",
    # wt-03 WU-2: the commit-review PHASE's own gate proofs (hard
    # tripwire, agent approval, reject citations, the stale-tree
    # re-review and the edit-step reseal) drive the real git boundary,
    # so the module carries the module-level ``subprocess_e2e`` marker.
    # Unregistered, static discovery DROPS the file and the default
    # marker expression deselects every item, so the proposal-seal
    # contracts (a stale verdict is re-reviewed; the verdict is
    # evaluated against the NEW seal the edit step produces) would rot
    # in the opt-in lane while ``make verify`` stayed green. The
    # dedicated real-git lane's own shards absorb the
    # file's measured cost (82 tests, ~20 CPU core-seconds serial,
    # ~5.9 s wall under the lane's shard profile) inside the
    # pinned 60 s combined budget.
    "tests/test_phases_commit_review.py",
    # wt-03-commit-review-flow: the commit-review REAL-GIT proof modules.
    # Both drive the real ``git`` boundary (a real tmp-path repository, the
    # real proposal seal, the real ``git diff-tree`` diff and the real
    # rollback), so they carry the module-level ``subprocess_e2e`` marker.
    # Unregistered, static discovery DROPS the file and the default marker
    # expression deselects every item, so the "a behaviour a recursive diff
    # never produces cannot pass vacuously" contract (the real-diff proofs)
    # and the rock-solid phase contract would rot in the opt-in lane while
    # ``make verify`` stayed green. Registering them keeps both proofs on
    # the default ``make test`` profile, where the dedicated real-git
    # lane's own shards absorb their measured cost.
    "tests/test_commit_review_real_diff_proofs.py",
    "tests/integration/test_commit_review_rock_solid.py",
    # wt-03-commit-review-flow: the remaining commit-review modules that
    # carry the module-level ``subprocess_e2e`` marker. The final-commit
    # review -> final commit transition has no other dedicated test in the
    # suite, and the git commit-review operation proofs drive the real
    # ``Repo``/``git`` boundary. Both were dropped by static discovery and
    # deselected by the default marker expression, so their coverage rotted
    # in the opt-in lane while ``make verify`` stayed green.
    "tests/integration/test_pipeline_final_commit_review_to_final_commit_e2e.py",
    "tests/test_git_commit_review.py",
    # wt-11: crash-safety and concurrency proofs for the indexed
    # exploration substrate (S-5). subprocess_e2e so the cross-process
    # boundary and atomic promotion are exercised; in-budget on the
    # 60s combined verify budget.
    "tests/test_explore_crash_safety.py",
    "tests/test_explore_concurrency.py",
    # wt-11 S-1: cold-start lifecycle regression (E1/E2/E3) is
    # subprocess_e2e (the stale-handle test must cross a process
    # boundary to be honest) and must stay on the default
    # make-test profile so the regression cannot rot silently.
    "tests/test_explore_cold_start_lifecycle.py",
    # wt-11 S-5: in-budget perf regression gate validates the
    # committed baseline JSON against the documented numeric
    # targets and the schema validator; the slow full-capture
    # layer lives in a dedicated subprocess_e2e test so this
    # file stays inside the 60s combined verify budget.
    "tests/test_explore_perf_regression_gate.py",
    # wt-12: the deterministic auto-commit acceptance cases for the
    # skill and policy surfaces are subprocess_e2e (real-git boundary
    # exercised end-to-end). Keep them on the default ``make test``
    # profile so the isolation / rollback contract cannot rot
    # silently. In-budget on the 60s combined verify budget.
    "tests/test_skills_auto_commit.py",
    # wt-12: the producer-level install + auto-commit boundary cases
    # (dir-to-symlink transition, byte-equal trap, prune +
    # sibling-symlink, unrelated-dirty-file guard, no-op second
    # run) are split from test_skills_auto_commit to keep the
    # shared-primitive test under the audit's 1000-line cap. The
    # split file runs in the same default make test profile so the
    # boundary regression coverage cannot rot silently.
    "tests/test_skills_install_auto_commit.py",
    "tests/project_policy/test_policy_auto_commit.py",
    # wt-12: the shared primitive's own real-git regression cases
    # (exact-path HEAD lookup, dir→symlink transition, FAILED
    # rollback, ancestor-symlink safety). Real-git boundary
    # required for the prefix-collision and dir→symlink shapes; the
    # default ``make test`` profile is the only way the contract
    # cannot rot silently.
    "tests/test_scoped_auto_commit.py",
    # wt-12: the deterministic auto-commit acceptance cases for the
    # config write sites (autowire, enable, init starter prompt). subprocess_e2e (real-git boundary exercised end-to-end)
    # but must stay on the default ``make test`` profile so the
    # config-write isolation / rollback contract cannot rot silently.
    # In-budget on the 60s combined verify budget.
    "tests/test_config_auto_commit.py",
    # wt-12: the bootstrap config-write isolation cases (Unit 1) and
    # the parameterized clean-tree acceptance cases for the
    # non-bootstrap deterministic writers (skill sync, skill install,
    # policy preflight, gitignore seed, config autowire). Real-git
    # subprocess_e2e surfaces that MUST stay on the default ``make
    # test`` profile so the clean-tree / fixed-subject contract cannot
    # rot silently. In-budget on the 60s combined verify budget.
    "tests/test_config_bootstrap_git_isolation.py",
    "tests/test_deterministic_writers_clean_tree.py",
    # wt-04 (U5-REPAIR-A): the config-read immutability proofs assert on
    # the real repo HEAD sha (and the agents file's sha256) after a config
    # load and after a ``--diagnose --config`` run, and the autowire proofs
    # assert on the real git log subjects (an agents file creates NO chore
    # commit; a main config DOES create one). Real-git boundary is the
    # subject under test, so both files are ``subprocess_e2e`` and MUST stay
    # on the default ``make test`` profile -- behind an opt-in gate the
    # "a config read never writes or commits" contract would rot silently.
    "tests/config/test_agents_file_immutable.py",
    "tests/config/test_init_agents_autowire.py",
    # wt-15-policy (U1): the parametrized display module was previously
    # 460 tests and wall-clock bottleneck; split into three coherent files
    # (catalog/surface, renderer/output, salience/idle-burst) to keep each
    # under the audit's 1000-line cap. All three files must remain on the
    # default make-test profile so the scene rendering contracts cannot rot.
    "tests/display/test_generated_scene_catalog.py",
    "tests/display/test_generated_scene_renderer.py",
    "tests/display/test_generated_scene_salience.py",
    # wt-03-fix-bug (S): the linked-worktree regression test for the
    # diagnostic-evidence collector. Real-git, in-budget, must run
    # on the default ``make test`` profile so the architecture's
    # prohibition of repository-global reads cannot rot silently.
    "tests/test_integration_diagnostic_worktree_isolation.py",
    "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py",
    # wt-03-fix-bug (WU-5): the package-wide ``live_tree`` regression
    # gates in the opts-key and kwargs-forwarding audits each scan
    # the full shipped tree. They are slow under a single pytest
    # worker (~6 s for opts-key drift, ~2.5 s for kwargs-forwarding)
    # and would tip a single-file shard past the 60 s combined
    # budget even when every other shard finishes inside it. Moving
    # them to the E2E lane gives its own shards
    # (``_REQUIRED_E2E_SHARD_COUNT`` capped by the host's cores)
    # enough headroom to absorb the scan, and keeps the regression
    # gates on the default ``make test`` profile so the silent-key
    # drop cannot rot. They are marked ``subprocess_e2e`` so the
    # test-policy audit treats the real-tree scan as an external
    # boundary rather than a black-box test seam.
    "tests/test_audit_opts_key_drift.py",
    "tests/test_audit_kwargs_forwarding.py",
    # wt-03-fix-bug (U4a): real-git regression pinning acceptance
    # scenarios 1 (linked worktree) and 2 (target movement) on the
    # public-boundary decision surface. Real-git, in-budget, must
    # run on the default ``make test`` profile so the linked-
    # worktree obligation retention cannot rot silently.
    "tests/test_authority_audit_scenario_1_2_worktree_target.py",
    # wt-16: root-cause + containment regression tests for the
    # MCP respawn bug (R1-R4, S1-S4).
    "tests/mcp/test_reserved_socket_held_across_respawn.py",
    "tests/mcp/test_mcp_endpoint_never_refuses.py",
    "tests/mcp/test_mcp_slow_startup_served_from_backlog.py",
    "tests/mcp/test_respawn_no_discovery.py",
    "tests/mcp/test_mcp_server_startup_stages_observable.py",
    "tests/mcp/test_mcp_server_last_stage_when_stage_file_missing.py",
    "tests/mcp/test_mcp_stage_attribution_real_child.py",
    "tests/mcp/test_restart_aware_bridge_failed_restart_fn.py",
    "tests/mcp/test_restart_aware_bridge_policy_cooldown.py",
    # wt-16 WU-4: additional respawn regression gates
    "tests/mcp/test_runtime_respawn_precheck.py",
    "tests/mcp/test_discovery_aggregate_budget.py",
    "tests/mcp/test_start_stage_tracker_fresh_spawn.py",
    "tests/mcp/test_preflight_error_carries_stage_and_elapsed.py",
    # wt-16 WU-R2-ENVELOPE: whole-startup budget envelope — child blocked at
    # registry_built surfaces typed McpServerError with last_stage and elapsed_s
    "tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py",
    # wt-16 WU-B follow-up: C1 step 2 effective-config fingerprint is
    # computed by the parent before the first spawn, persisted in the
    # snapshot, and matched by the candidate on the private readiness
    # channel. Mismatch refuses promotion while the old child is alive;
    # dead-old-child escapes promote with a warning. Tests are fake-based
    # and exercised under the default ``make test`` profile so the C1
    # step 2 contract cannot rot silently. In-budget on the 60s combined
    # verify budget (5 tests, ~0.5s wall).
    "tests/mcp/test_effective_config_fingerprint.py",
    # wt-16 (WU-B follow-up): both files shell out to real git in tmp_path
    # repos and now carry the ``subprocess_e2e`` module marker (enforced by
    # the process-audit AST exemption), so they MUST be registered here to
    # stay on the default ``make test`` profile — otherwise discovery
    # excludes them and their coverage silently rots.
    "tests/test_display_lifecycle_label_taxonomy.py",
    "tests/test_unreadable_record_entry_points.py",
    # wt-16: the CLI-child banner visibility regression. The parent does
    # not pre-set PYTHONUNBUFFERED, so the child must reach stdout via the
    # flush + line_buffering reconfigure wired in runtime.py and
    # _fallback_standalone_server.py. subprocess_e2e because it crosses
    # a real process boundary. On the default make-test profile so the
    # banner regression cannot rot.
    "tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py",
    # wt-16 WU-1 / Unit V: real-child startup budget, bounded catalog publish,
    # and fail-closed inherited FD adoption regressions. These cross a real
    # process boundary (the FIFO-blocked open/session tests must reach the
    # fail-closed overrun helpers inside a real child, and the invalid-inherited-FD
    # tests prove the typed McpServerError reaches the parent as a non-zero exit).
    # Registered on the default profile in the dedicated E2E lane (10 shards)
    # so the fail-closed real-child proofs cannot rot silently and fit within the
    # 60s immutable combined test budget.
    "tests/mcp/test_whole_startup_budget_production.py",
    "tests/mcp/test_bounded_catalog_publish.py",
    "tests/mcp/test_inherited_fd_invalid_fails_closed.py",
    # wt-16 WU-R4: wire-ledger fail-closed OSError path — append_wire_record
    # raises WireLedgerIOError on OSError, surfaced as JSON-RPC -32603.
    # Must stay on the default make-test profile so the fail-closed contract
    # cannot rot silently.
    "tests/mcp/test_wire_ledger_fail_closed.py",
    # wt-16 WU-Probe: probe-overrun self-measurement — overrun past deadline +
    # slack is classified as contention (C3); sustained overrun ceiling triggers
    # forced replacement.
    "tests/mcp/test_probe_overrun_self_measure.py",
    # wt-16 WU-Bridge: ATTEMPT_FAILED is non-terminal; only BUDGET_EXHAUSTED
    # raises McpServerError. The C2 fix pins the new contract so a single
    # failed restart_fn call does NOT end the agent run.
    "tests/mcp/test_bridge_attempt_vs_budget.py",
    # wt-16 make-before-break MCP restart resilience test suites:
    "tests/mcp/test_bridge_make_before_break.py",
    "tests/mcp/test_bridge_e1_e2_edge_cases.py",
    "tests/mcp/test_bridge_e3_e4_edge_cases.py",
    "tests/mcp/test_bridge_e5_e6_e7_edge_cases.py",
    # wt-16 make-before-break MCP edge case & integration test suites:
    "tests/mcp/test_e16_store_interleave_real_children.py",
    "tests/mcp/test_e17_state_continuity.py",
    "tests/mcp/test_e18_old_session_id_accepted.py",
    "tests/mcp/test_e24_fresh_stage_id.py",
    "tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py",
    "tests/mcp/test_e26_broken_workspace_package.py",
    "tests/mcp/test_e33_listening_fd_real_child.py",
    "tests/mcp/test_e3x_transport_gap_typed_retry.py",
    "tests/mcp/test_e42_two_supervisors_one_replacement.py",
    "tests/mcp/test_e46_parent_sigkill_no_orphans.py",
    "tests/mcp/test_e64_import_guard_execution.py",
    "tests/mcp/test_e72_footprint_real_child.py",
    "tests/mcp/test_handover_inflight_real_child.py",
    "tests/mcp/test_replacement_shutdown_signals_real_child.py",
    "tests/mcp/test_root_cause_real_child_bridge.py",
    "tests/mcp/test_s1_independent_counters_contract.py",
    "tests/mcp/test_single_acceptor_instrumented.py",
    # wt-16 WU-Plan: spawn-environment helper extraction regresses the typed-error
    # boundary for ``_spawn_mcp_process`` so a fault inside the helper before
    # ``deps.spawn_process`` propagates the typed error (no UnboundLocalError),
    # leaves the session file removed, and closes both socket ends it owns.
    # subprocess_e2e because the helper boundary is real-child-coupled.
    "tests/mcp/test_spawn_early_failure_typed.py",
    # wt-19-fix-long-exec-tasks — exec job flow smokes (real subprocess)
    # Real-process proofs for the liveness assessor + job registry. Both
    # files carry subprocess_e2e + required_auto_integrate_e2e markers.
    "tests/test_exec_smoke_subprocess_e2e.py",
    "tests/test_exec_job_shutdown_subprocess_e2e.py",
)

#: Default-lane files sanctioned by the GitPython real-Git boundary audit
#: (``ralph.testing.audit_test_policy``, added in ``223d8ab65``). This is
#: the AUDIT-exception registry and must stay disjoint in ROLE from
#: ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` above: that registry is also the
#: pytest SELECTION requirement, so it may only name files discovery can
#: select (``test_*.py`` / ``*_test.py``) -- ``tests/conftest.py`` can
#: never be discovered and would fail the gate unconditionally. Both
#: registries are consulted by the audit's ``_is_registered_real_git_file``
#: so the sanction and the selection requirement cannot drift. The files
#: below predate the audit and run real ``Repo.init`` / ``git.Repo``
#: crossings in the default ``make test`` profile; each remains a
#: default-lane file (no ``subprocess_e2e`` marker is added or removed).
#:
#: Because the audit honours BOTH registries, a file already named in the
#: selection registry is sanctioned automatically; naming it here as well
#: would give one file two roles and break the disjointness invariant
#: pinned by ``tests/test_test_suites.py``. wt-04 (U5-REPAIR-A) therefore
#: keeps the two agents-config proofs
#: (``tests/config/test_agents_file_immutable.py`` and
#: ``tests/config/test_init_agents_autowire.py``) in the SELECTION
#: registry only -- their real-Git crossings stay sanctioned through it,
#: and their proofs stay on the default ``make test`` profile.
#:
#: wt-03-commit-review-flow applies the same rule to
#: ``tests/test_commit_review_real_diff_proofs.py``: it carries the
#: module-level ``subprocess_e2e`` marker, so it is not a default-lane
#: file, and the AUDIT-exception registry would sanction a real-Git
#: crossing whose proofs do not run on the default profile. It lives in the
#: SELECTION registry only, where the registration both sanctions the
#: crossing and admits the proofs to the default ``make test`` profile.
SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES: tuple[str, ...] = (
    "tests/conftest.py",
    "tests/pipeline/test_reducer_lock_retry.py",
    "tests/test_auto_integrate_gates.py",
    "tests/test_bootstrap_auto_seed_git_exclude.py",
    "tests/test_bootstrap_auto_seed_gitignore.py",
    "tests/test_commit_review_diff.py",
    "tests/test_commit_review_reject_citation_gate.py",
    "tests/test_commit_review_tripwires.py",
    "tests/test_commit_detached_head_recovery.py",
    "tests/test_commit_executor.py",
    "tests/test_git_proposal_seal.py",
    "tests/integration/test_commit_review_gate_e2e.py",
    "tests/test_cycle_baseline_diff_cycle_baseline_diff.py",
    "tests/test_cycle_baseline_diff_runner_cycle_start_uses_force_true.py",
    "tests/test_cycle_baseline_diff_write_cycle_baseline_force_param.py",
    "tests/test_git_command_queue.py",
    "tests/test_git_wrapper.py",
    "tests/test_integration_diagnostic_livelock.py",
    "tests/test_pipeline_runner_execute_commit_effect.py",
    "tests/test_prompt_materialize_2.py",
    "tests/test_prompt_materialize_3.py",
    "tests/test_worktree_lease.py",
)
_VERIFICATION_MARK_EXPRESSION = "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
#: Registry entries that are pytest PLUGIN modules rather than selectable
#: test modules.
#:
#: ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` serves two roles: it is the
#: selection registry for the default lane AND the sanctioned exemption
#: registry for the real-git audit
#: (``ralph.testing.audit_test_policy._is_registered_real_git_file``).
#: ``tests/conftest.py`` belongs to the second role only: it holds shared
#: fixtures and performs a real ``Repo.init()`` at import time, but pytest's
#: static discovery (``test_*.py`` / ``*_test.py``) can never select it.
#: Requiring it in the selection made the default lane unsatisfiable (the
#: fail-closed check could never pass), and handing it to pytest as an
#: explicit argument would collect a plugin module as a test module. It is
#: therefore excluded from every selection requirement and from every
#: explicit pytest argument list, while it stays registered for the audit.
NON_SELECTABLE_REQUIRED_REGISTRY_FILES: frozenset[str] = frozenset({"tests/conftest.py"})
_SUBPROCESS_E2E_MARK_EXPRESSION = (
    "subprocess_e2e and not smoke and not live_agy and not verify_budget_real_time"
)
_FOCUSED_PROFILE_MARK_EXPRESSION = "not subprocess_e2e and not smoke"
_UNIT_PROFILE = "unit"
_INTEGRATION_PROFILE = "integration"
_FAST_PROFILE = "fast"
FAST_PROFILE_TEST_FILES: tuple[str, ...] = (
    "tests/test_makefile_verification_workflow.py",
    "tests/test_test_suites.py",
    "tests/test_test_suites_orchestration.py",
)
_FOCUSED_PROFILES = frozenset((_UNIT_PROFILE, _INTEGRATION_PROFILE, _FAST_PROFILE, "durations"))
_DURATIONS_PROFILE = "durations"
_DURATIONS_PER_FILE_TIMEOUT_SECONDS = 30.0
_PROFILE_OPTION_ARGUMENT_COUNT = 2
_SHARD_POLL_INTERVAL_SECONDS = 0.01
# DA-003 (wt-028-display P1 / AC-08 / S-13): when the 60s parent
# deadline fires, the runner has already given us its 60s budget;
# we only get back the budget tracker wall time if we exit
# within the remaining 0-5s of drain. The previous 5s drain
# routinely pushed the cumulative wall time over the immutable
# 60s budget even when the slowest shard was well under 60s.
# 1s is the lower bound that lets pytest print its summary line
# before SIGKILL; shorter drain values produce empty ``pytest shard
# did not exit after termination`` banners.
# wt-059 DA-005: the ``run_verify`` cumulative budget is CHARGED at the
# whole-step granularity: every tracked step must fit inside its own
# 60 s cap AND leave room for the other tracked steps. The multiplier
# was reduced from 120 to 60 when the 120x value starved unit-test
# shards; it was further reduced to 1 (no isolation) because the two
# REQUIRED files use per-test ``tmp_path`` fixtures and distribute
# across shards safely. Keeping any multiplier above 1 created a
# partition whose slowest shard exceeded the 50 s make-test step.
_SHARD_TERMINATION_DRAIN_SECONDS = 1.0
_REQUIRED_E2E_WEIGHT_MULTIPLIER = 1
#: Number of slowest tests reported by ``pytest --durations=N`` on each
#: shard. The runner always passes this flag so when a shard is killed
#: by the 60s combined budget, the captured stdout still names the
#: heaviest tests in that shard. The flag adds a sub-second timing
#: pass after the slowest call is recorded; the overhead is bounded
#: by the maintained test-suite size and is negligible relative to
#: the wall-clock saving it produces on the next bottleneck hunt.
#: Set to ``0`` to disable (no slowest-test reporting).
_PYTEST_DURATIONS_REPORT: int = 10


# The required real-git files run as a dedicated lane of plain-pytest shards
# that start alongside the general shards.
#
# wt-03-fix-bug originally ran them in ONE shard with four xdist workers, to
# hold the total concurrent process count at 12 (8 general + 4 E2E) on the
# then-maintained 12-core host. The in-shard fan-out was then raised to ten
# workers, because the single real-git shard -- not the general shards --
# set the ``make test`` wall clock: it ran 44 s of pytest work for 721
# collected tests, and ``make test`` + ``make test-verification-smoke``
# overflowed the immutable cumulative 60 s budget.
#
# The fan-out was the wrong tool for that job. Every xdist worker collects
# the WHOLE shard manifest, so ten workers imported the conftest chain and
# all 90 real-git modules ten times over: measured on the maintained host,
# one 10-worker xdist shard cost 423.3 CPU core-seconds while the same 90
# files spread over ten plain shards cost 388.3 -- ~35 core-seconds of
# duplicated collection and import that the immutable 700 core-second
# budget is charged for.
#
# Ten plain shards give the same parallelism (each shard runs concurrently
# with the general shards) with ONE import of every module, and the
# recalibrated weight table balances them: the ten shards' predicted loads
# differ by 0.4 %, against a 1.29 spread between the general lane's shards
# under the old count-based weights.
_REQUIRED_E2E_SHARD_COUNT = 10


def _required_e2e_shard_count() -> int:
    """Return the number of plain-pytest shards for the required real-git lane.

    The count is a CAP: a host with fewer cores gets one shard per core (a
    4-core CI runner given ten shards was oversubscribed three times over),
    while the 12-core and 40-core hosts keep all ten.
    """
    available_cores = os.cpu_count() or 1
    return min(_REQUIRED_E2E_SHARD_COUNT, available_cores)


def _exclusive_subprocess_e2e_xdist_workers() -> str:
    """Return the in-shard xdist count for the exclusive subprocess-E2E shard.

    The exclusive subprocess-E2E file (``test_terminal_state_restored_on_exit.py``)
    contains eight real-PTY regressions, each spawning a fresh Python child
    that imports the ralf package (~1.5s of import time per child).  Without
    in-shard parallelism the file needs ~13s of wall clock, which exceeds
    the 60s combined budget once the general subprocess shards finish
    (~55s on the maintained host).  Four in-shard xdist workers cut the
    file to ~7s while preserving every behavioral assertion -- the PTY
    and termios state is per-test, so concurrent tests are safe.  The cap
    matches ``_MAX_XDIST_WORKERS_PER_SHARD`` and is host-invariant.
    """
    return "4"


_EXCLUSIVE_SUBPROCESS_E2E_SHARD_XDIST_WORKERS = _exclusive_subprocess_e2e_xdist_workers()
_PARAMETRIZE_CASES_ARGUMENT_INDEX = 1
#: Wall-clock seconds one weight unit stands for. The weight table is
#: calibrated at 17 units per second (``round(measured_seconds * 17)``), so
#: the count-based estimate is scaled into the SAME units: a file whose
#: collected-test count is its only estimate is weighted as if each of its
#: tests took ``_BASE_SECONDS_PER_TEST`` seconds.
_WEIGHT_SECONDS_PER_UNIT: Final = 1.0 / 17.0
#: Measured seconds per collected test for a file with no measured override.
#: The maintained suite's fast modules spend ~5 ms per test; the general
#: lane's mean measured cost is 0.03 s per test, so this factor keeps the
#: count-based files on the same time scale as the measured overrides
#: without letting a fast file outweigh a measured heavy one.
_BASE_SECONDS_PER_TEST: Final = 0.005
#: Per-file load weights for the LPT shard partition.
#:
#: One weight unit is 1/17 of a second of measured wall clock: the scale the
#: historical calibration used (``weight = round(measured_seconds * 17)``).
#: The values are MEASURED per-file durations from the maintained suite's
#: shard runs, NOT test counts. The count-based estimate mis-places the heavy
#: real-git, MCP and display modules, which left the eight general shards
#: with a 1.29 spread between the fastest and the slowest shard and pushed
#: the slowest shard (75.5 s) past the combined wall budget.
#:
#: Calibration rule: a file whose measured duration reaches 0.5 s gets
#: ``round(measured_seconds * 17)``; a file whose every phase ran below the
#: 5 ms durations-reporting floor keeps its hand-calibrated weight, because
#: the reported sum undercounts it. Files outside this table use the
#: count-based estimate scaled into the same time units by
#: ``_BASE_SECONDS_PER_TEST``.
#:
#: The required real-git files are measured from the plain-shard run of the
#: dedicated real-git lane, so the same table balances that lane's shards.
_DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES: Mapping[str, int] = {
    "tests/agents/invoke/test_invoke_monitor_wiring.py": 13,
    "tests/agents/invoke/test_runtime_resolver_endpoint_delivery.py": 11,
    "tests/agents/test_invoke_timeout_integration.py": 28,
    "tests/agents/test_terminal_state_restored_on_exit.py": 270,
    "tests/agents/test_workspace_event_backpressure.py": 12,
    "tests/config/test_agents_file_immutable.py": 61,
    "tests/config/test_agents_normal_run_immutable.py": 13,
    "tests/config/test_agents_toml_fallback.py": 22,
    "tests/config/test_check_config_reports_every_unreadable_file.py": 24,
    "tests/config/test_init_agents_autowire.py": 10,
    "tests/config/test_regenerate_agents_safe.py": 9,
    "tests/config/test_retention_max_age_config.py": 14,
    "tests/display/test_generated_scene_catalog.py": 13,
    "tests/display/test_generated_scene_renderer.py": 31,
    "tests/display/test_generated_scene_salience.py": 9,
    "tests/integration/test_commit_review_baseline_e2e.py": 16,
    "tests/integration/test_commit_review_gate_e2e.py": 84,
    "tests/integration/test_commit_review_gate_failure_e2e.py": 16,
    "tests/integration/test_legacy_restart_acceptance.py": 21,
    "tests/integration/test_lifecycle_restart_acceptance.py": 71,
    "tests/integration/test_mcp_exec_result_contract.py": 21,
    "tests/integration/test_pipeline_commit_review_to_commit_e2e.py": 103,
    "tests/integration/test_retained_integration_acceptance.py": 92,
    "tests/integration/test_runner_default_display.py": 24,
    "tests/integration/test_runner_fanout_wiring.py": 10,
    "tests/integration/test_single_agent_e2e.py": 23,
    "tests/integration/test_transcript_end_to_end.py": 57,
    "tests/integration/test_validate_custom_mcp_e2e.py": 16,
    "tests/mcp/test_boot_allowance_lifecycle.py": 9,
    "tests/mcp/test_bounded_catalog_publish.py": 56,
    "tests/mcp/test_bridge_e1_e2_edge_cases.py": 23,
    "tests/mcp/test_bridge_make_before_break.py": 79,
    "tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py": 29,
    "tests/mcp/test_codex_config_synthesis.py": 11,
    "tests/mcp/test_e17_state_continuity.py": 43,
    "tests/mcp/test_e18_old_session_id_accepted.py": 19,
    "tests/mcp/test_e3x_transport_gap_typed_retry.py": 234,
    "tests/mcp/test_e42_two_supervisors_one_replacement.py": 47,
    "tests/mcp/test_e46_parent_sigkill_no_orphans.py": 62,
    "tests/mcp/test_e54_bridge_zombie.py": 13,
    "tests/mcp/test_e64_import_guard_execution.py": 96,
    "tests/mcp/test_e72_footprint_real_child.py": 63,
    "tests/mcp/test_handover_inflight_real_child.py": 103,
    "tests/mcp/test_inherited_fd_invalid_fails_closed.py": 58,
    "tests/mcp/test_mcp_activity_relay_sequence_anomaly.py": 11,
    "tests/mcp/test_mcp_endpoint_never_refuses.py": 78,
    "tests/mcp/test_mcp_slow_startup_served_from_backlog.py": 61,
    "tests/mcp/test_mcp_stage_attribution_real_child.py": 26,
    "tests/mcp/test_replacement_shutdown_signals_real_child.py": 89,
    "tests/mcp/test_respawn_no_discovery.py": 199,
    "tests/mcp/test_root_cause_real_child_bridge.py": 291,
    "tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py": 25,
    "tests/mcp/test_runtime_blocked_workspace_read_surfaces_typed_error.py": 10,
    "tests/mcp/test_single_acceptor_instrumented.py": 48,
    "tests/mcp/test_whole_startup_budget_production.py": 120,
    "tests/pipeline/test_auto_integrate_agent_failure.py": 220,
    "tests/pipeline/test_commit_retry_real_route.py": 18,
    "tests/pipeline/test_conflict_resolution_ledger.py": 9,
    "tests/pipeline/test_conflict_resolution_unresolved_typed.py": 9,
    "tests/pipeline/test_continuation_retained_reason_gate.py": 22,
    "tests/pipeline/test_continuation_retry_gate.py": 79,
    "tests/pipeline/test_continuation_retry_gate_blackbox.py": 136,
    "tests/pipeline/test_corrupt_agents_toml_repro.py": 11,
    "tests/pipeline/test_development_phase_missing_plan_handoff_status_bar_visible.py": 8,
    "tests/pipeline/test_paused_rebase_config_recovery_blackbox.py": 180,
    "tests/pipeline/test_planning_commit_recovery.py": 11,
    "tests/pipeline/test_planning_requires_target_sync.py": 40,
    "tests/pipeline/test_reducer_lock_retry.py": 21,
    "tests/pipeline/test_run_loop_resume_prefers_available_agent.py": 29,
    "tests/pipeline/test_terminal_mcp_error_per_entry_path.py": 21,
    "tests/project_policy/test_completed_upgrades.py": 11,
    "tests/project_policy/test_fresh_init_v6.py": 12,
    "tests/project_policy/test_pipeline_driver.py": 16,
    "tests/project_policy/test_policy_auto_commit.py": 26,
    "tests/project_policy/test_policy_handback_channel_ledger.py": 29,
    "tests/project_policy/test_policy_handback_ready_route.py": 23,
    "tests/project_policy/test_policy_handback_to_pipeline.py": 20,
    "tests/project_policy/test_policy_handback_working_tree.py": 92,
    "tests/project_policy/test_policy_never_blocks_the_run.py": 34,
    "tests/project_policy/test_preflight.py": 11,
    "tests/project_policy/test_starter_enforcement_prose.py": 9,
    "tests/project_policy/test_v4_corpus_authentic.py": 11,
    "tests/project_policy/test_validator.py": 20,
    "tests/recovery/test_agent_failure_charge_once.py": 31,
    "tests/recovery/test_agent_failure_route_facts.py": 18,
    "tests/recovery/test_agent_failure_scenarios.py": 79,
    "tests/recovery/test_ledger_migration.py": 34,
    "tests/recovery/test_no_progress_quiet.py": 9,
    "tests/recovery/test_run_loop_real_resume_restart.py": 16,
    "tests/recovery/test_runner_offline_pause_resume.py": 22,
    "tests/recovery/test_runner_recovery_end_to_end.py": 44,
    "tests/recovery/test_single_agent_broken_chain_failfast.py": 60,
    "tests/test_agent_chain.py": 11,
    "tests/test_agents_invoke.py": 31,
    "tests/test_analysis_prompt_payload_contract_retry_hint_guard_in_templates.py": 12,
    "tests/test_audit_opts_key_drift.py": 280,
    "tests/test_audit_terminal_escape_containment.py": 29,
    "tests/test_audit_test_policy.py": 22,
    "tests/test_authority_audit_scenario_3_4_9_restart_retry.py": 43,
    "tests/test_auto_integrate_acceptance_scenarios_e2e.py": 210,
    "tests/test_auto_integrate_boundary_cleanliness.py": 48,
    "tests/test_auto_integrate_boundary_refresh.py": 177,
    "tests/test_auto_integrate_catchup_remote_coordination.py": 44,
    "tests/test_auto_integrate_conflict_budget.py": 180,
    "tests/test_auto_integrate_drift_recovery.py": 13,
    "tests/test_auto_integrate_end_to_end.py": 40,
    "tests/test_auto_integrate_fail_closed_e2e.py": 13,
    "tests/test_auto_integrate_gates.py": 208,
    "tests/test_auto_integrate_local_fleet_target_e2e.py": 55,
    "tests/test_auto_integrate_race.py": 140,
    "tests/test_auto_integrate_real_agent_resolution_e2e.py": 100,
    "tests/test_auto_integrate_record_loader_lease.py": 14,
    "tests/test_auto_integrate_recovery_git_state_reflog.py": 27,
    "tests/test_auto_integrate_remote_refresh.py": 300,
    "tests/test_auto_integrate_remote_sync_fast_forward_contract.py": 252,
    "tests/test_auto_integrate_remote_sync_pull.py": 46,
    "tests/test_auto_integrate_remote_sync_push.py": 25,
    "tests/test_auto_integrate_resolution.py": 165,
    "tests/test_auto_integrate_resolving_rebase_record.py": 13,
    "tests/test_auto_integrate_retained_recovery_seam.py": 68,
    "tests/test_auto_integrate_skip_reasons.py": 13,
    "tests/test_auto_rebase_idempotence_e2e.py": 60,
    "tests/test_backup_ref_operation_scope.py": 44,
    "tests/test_bootstrap_auto_seed_gitignore.py": 17,
    "tests/test_branch_relation_matrix.py": 78,
    "tests/test_cli_commands_1.py": 33,
    "tests/test_cli_commands_init.py": 14,
    "tests/test_cli_commands_run_skill_sync.py": 15,
    "tests/test_cli_commit_command.py": 9,
    "tests/test_cli_diagnose_path.py": 46,
    "tests/test_cli_long_mode_semantics.py": 14,
    "tests/test_cli_quick_mode_semantics.py": 11,
    "tests/test_cli_removed_review_flags.py": 9,
    "tests/test_cli_run_skill_sync_warnings.py": 10,
    "tests/test_cli_subcommand_early_return_proof.py": 11,
    "tests/test_commit_detached_head_recovery.py": 217,
    "tests/test_commit_executor.py": 169,
    "tests/test_commit_review_diff.py": 31,
    "tests/test_commit_review_invariants.py": 126,
    "tests/test_commit_review_reject_citation_gate.py": 36,
    "tests/test_commit_review_tripwires.py": 44,
    "tests/test_commit_review_verify_gate.py": 149,
    "tests/test_completion_invocation_exit_logging.py": 14,
    "tests/test_config_auto_commit.py": 45,
    "tests/test_config_bootstrap.py": 61,
    "tests/test_config_bootstrap_git_isolation.py": 49,
    "tests/test_conflict_resolution_attempt_attribution.py": 16,
    "tests/test_conflict_resolution_futile_loop.py": 23,
    "tests/test_conflict_resolution_gitlink_escalation.py": 210,
    "tests/test_conflict_resolution_phase_parity.py": 12,
    "tests/test_conflict_resolution_pipeline.py": 83,
    "tests/test_conflict_resolution_pipeline_outcome.py": 12,
    "tests/test_cursor_runtime_auth_preservation.py": 10,
    "tests/test_deterministic_writers_clean_tree.py": 27,
    "tests/test_developer_prompt_subagent_guidance.py": 9,
    "tests/test_display_single_source_formatters.py": 15,
    "tests/test_exec_job_integration.py": 34,
    "tests/test_exec_job_shutdown_subprocess_e2e.py": 207,
    "tests/test_exec_jobs.py": 188,
    "tests/test_exec_jobs_finish_regressions.py": 54,
    "tests/test_exec_jobs_lifecycle.py": 12,
    "tests/test_exec_liveness_scenarios.py": 16,
    "tests/test_exec_smoke_subprocess_e2e.py": 79,
    "tests/test_executor_process.py": 12,
    "tests/test_explore_bench_rss_isolation.py": 42,
    "tests/test_explore_cold_start_lifecycle.py": 20,
    "tests/test_explore_concurrency.py": 12,
    "tests/test_explore_crash_safety.py": 35,
    "tests/test_explore_fault_matrix.py": 18,
    "tests/test_explore_fault_matrix_full.py": 11,
    "tests/test_explore_graph.py": 10,
    "tests/test_explore_handlers.py": 11,
    "tests/test_explore_mcp_timeout.py": 13,
    "tests/test_explore_perf_regression_gate.py": 109,
    "tests/test_explore_pipeline.py": 33,
    "tests/test_explore_real_codebase.py": 77,
    "tests/test_filesystem_scenario_baseline.py": 18,
    "tests/test_git_proposal_seal.py": 46,
    "tests/test_git_wrapper.py": 16,
    "tests/test_harness_run_diagnosis.py": 10,
    "tests/test_install_wheel.py": 120,
    "tests/test_integration_clock_and_reasons.py": 11,
    "tests/test_integration_decision_table.py": 119,
    "tests/test_integration_diagnostic.py": 21,
    "tests/test_integration_diagnostic_evidence.py": 29,
    "tests/test_integration_diagnostic_exhaustion.py": 28,
    "tests/test_integration_diagnostic_intervention_cap.py": 26,
    "tests/test_integration_diagnostic_livelock.py": 44,
    "tests/test_integration_diagnostic_resolver_health.py": 166,
    "tests/test_integration_diagnostic_worktree_isolation.py": 18,
    "tests/test_integration_dirt_decisions.py": 12,
    "tests/test_integration_dirt_shapes.py": 21,
    "tests/test_integration_fault_catalogue.py": 143,
    "tests/test_integration_fault_model.py": 74,
    "tests/test_integration_ladder.py": 10,
    "tests/test_integration_ladder_last_resort.py": 14,
    "tests/test_integration_obstruction_real_git.py": 490,
    "tests/test_integration_seam_wiring.py": 15,
    "tests/test_integration_state_coverage.py": 128,
    "tests/test_integration_state_space.py": 69,
    "tests/test_integration_termination_model.py": 14,
    "tests/test_internal_skills_mcp_prompts.py": 15,
    "tests/test_lifecycle_acceptance.py": 22,
    "tests/test_liveness_pa002_properties.py": 21,
    "tests/test_liveness_properties.py": 36,
    "tests/test_liveness_termination.py": 20,
    "tests/test_mcp_endpoint_functional_sweep.py": 90,
    "tests/test_mcp_lifecycle.py": 18,
    "tests/test_mcp_server_exec_result_contract.py": 19,
    "tests/test_md_artifact_editing.py": 9,
    "tests/test_multimodal_cli_parity.py": 11,
    "tests/test_no_anti_drift_recovery_invariants.py": 13,
    "tests/test_no_anti_drift_regression.py": 79,
    "tests/test_no_production_intervention_outcome.py": 24,
    "tests/test_no_superseded_paths_typed_setter_contract.py": 21,
    "tests/test_operation_evidence.py": 9,
    "tests/test_parallel_display.py": 14,
    "tests/test_parallel_display_content_routing.py": 13,
    "tests/test_pending_merge_commit_recovery.py": 80,
    "tests/test_pending_merge_repair.py": 12,
    "tests/test_pending_merge_repair_real_git.py": 147,
    "tests/test_pending_merge_repair_resolver_health.py": 15,
    "tests/test_per_test_budget.py": 36,
    "tests/test_per_worker_logging.py": 9,
    "tests/test_phases_commit_review.py": 352,
    "tests/test_phases_retry_on_stale_session.py": 16,
    "tests/test_pipeline_orchestration_flow.py": 13,
    "tests/test_pipeline_runner.py": 21,
    "tests/test_pipeline_runner_determine_effect.py": 10,
    "tests/test_pipeline_runner_execute_agent_effect_2_a.py": 38,
    "tests/test_pipeline_runner_execute_commit_effect.py": 98,
    "tests/test_pipeline_runner_pipeline_runner_loop_1.py": 129,
    "tests/test_pipeline_runner_timeout.py": 13,
    "tests/test_pipeline_session_bridge.py": 13,
    "tests/test_pro_support_contract.py": 9,
    "tests/test_process_audit.py": 9,
    "tests/test_process_hardening.py": 27,
    "tests/test_prompt_endpoint_advertising.py": 29,
    "tests/test_prompt_materialize_2.py": 30,
    "tests/test_prompt_materialize_3.py": 27,
    "tests/test_prompt_materialize_partial_development_result.py": 17,
    "tests/test_property_a_one_transport_one_behavior.py": 15,
    "tests/test_pty_line_reader_pending_bound.py": 12,
    "tests/test_public_workflow_crash_restart_matrix.py": 18,
    "tests/test_public_workflow_entry_point_matrix.py": 176,
    "tests/test_raw_overflow_display_writes_stay_jsonl.py": 9,
    "tests/test_real_config_guard.py": 37,
    "tests/test_rebase_first_order.py": 15,
    "tests/test_rebase_state_setters_owner.py": 8,
    "tests/test_record_verify_run.py": 38,
    "tests/test_recovery_release_uses_record_store.py": 36,
    "tests/test_retained_resolution_dispatch.py": 103,
    "tests/test_retained_resolution_handoff.py": 474,
    "tests/test_run_loop_pro_integration.py": 11,
    "tests/test_run_loop_retry_diagnostic.py": 72,
    "tests/test_run_loop_setters_only.py": 24,
    "tests/test_runner_auto_integrate_seam.py": 11,
    "tests/test_runner_decision_delegation.py": 23,
    "tests/test_runner_mcp_validation.py": 9,
    "tests/test_runner_output_routing.py": 26,
    "tests/test_runner_phase_transition_banner.py": 18,
    "tests/test_runner_quiet_mode.py": 38,
    "tests/test_scoped_auto_commit.py": 37,
    "tests/test_skills_auto_commit.py": 34,
    "tests/test_skills_content.py": 9,
    "tests/test_skills_install_auto_commit.py": 65,
    "tests/test_skills_installer_baseline.py": 16,
    "tests/test_skills_installer_sibling_symlinks.py": 21,
    "tests/test_skills_installer_update.py": 19,
    "tests/test_stale_intent_startup_replay.py": 91,
    "tests/test_timing_safety_transports.py": 28,
    "tests/test_tool_catalog_cache.py": 9,
    "tests/test_tool_exec_handle_exec_command.py": 30,
    "tests/test_tool_exec_process_cleanup.py": 11,
    "tests/test_tool_exec_run_command.py": 23,
    "tests/test_tool_git_read_nested_repo.py": 10,
    "tests/test_tool_git_read_path_validation.py": 44,
    "tests/test_unreadable_record_entry_points.py": 84,
    "tests/test_verify.py": 40,
    "tests/test_verify_budget.py": 71,
    "tests/test_visual_capture.py": 11,
    "tests/test_waiting_state_owner.py": 11,
    "tests/testing/test_audit_sast.py": 95,
    "tests/unit/display/test_palette.py": 17,
    "tests/unit/test_storage_lifecycle.py": 14,
}


if not EXCLUSIVE_SUBPROCESS_E2E_FILES:
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not be empty")
if len(EXCLUSIVE_SUBPROCESS_E2E_FILES) != len(set(EXCLUSIVE_SUBPROCESS_E2E_FILES)):
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not contain duplicates")
if not REQUIRED_AUTO_INTEGRATE_E2E_FILES:
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not be empty")
if len(REQUIRED_AUTO_INTEGRATE_E2E_FILES) != len(set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)):
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not contain duplicates")
if not SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES:
    raise RuntimeError("SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES must not be empty")
if len(SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES) != len(set(SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES)):
    raise RuntimeError("SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES must not contain duplicates")
if _MAX_PYTEST_WORKERS <= 0:
    raise RuntimeError("_MAX_PYTEST_WORKERS must be positive")
if _MAX_XDIST_WORKERS_PER_SHARD < 0:
    raise RuntimeError("_MAX_XDIST_WORKERS_PER_SHARD must be non-negative")
if _REQUIRED_E2E_SHARD_COUNT <= 0:
    raise RuntimeError("_REQUIRED_E2E_SHARD_COUNT must be positive")


def validate_exclusive_subprocess_e2e_selection(selected_files: Iterable[str]) -> None:
    """Fail unless each exclusive subprocess-E2E file is selected exactly once."""
    counts = Counter(selected_files)
    missing = tuple(path for path in EXCLUSIVE_SUBPROCESS_E2E_FILES if counts[path] == 0)
    duplicate = tuple(path for path in EXCLUSIVE_SUBPROCESS_E2E_FILES if counts[path] > 1)
    if missing or duplicate:
        details = []
        if missing:
            details.append("missing files: " + ", ".join(missing))
        if duplicate:
            details.append("duplicate files: " + ", ".join(duplicate))
        raise RuntimeError("invalid exclusive subprocess-E2E selection: " + "; ".join(details))


def validate_required_auto_integrate_selection(selected_files: Iterable[str]) -> None:
    """Fail when combined pytest selection omits a required E2E file."""
    selected = frozenset(selected_files)
    missing = tuple(
        path
        for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES
        if path not in NON_SELECTABLE_REQUIRED_REGISTRY_FILES and path not in selected
    )
    if missing:
        raise RuntimeError(
            "combined pytest selection omitted required auto-integrate E2E files: "
            + ", ".join(missing)
        )


def partition_selected_files(
    selected_files: Iterable[str],
    *,
    worker_count: int,
    file_weights: Mapping[str, int] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Partition selected test files deterministically across workers.

    Required auto-integration E2E files and remaining files use the same
    deterministic largest-processing-time (LPT) placement. This isolates a
    heavy real-git file instead of round-robinning it onto an already loaded
    shard, minimizing the maximum predicted shard load. Exact-once assignment
    and deterministic weighting are preserved by sorting by weight then path.
    """
    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    ordered_files: tuple[str, ...] = tuple(sorted(set(selected_files)))
    if not ordered_files:
        return ()
    shard_count = min(worker_count, len(ordered_files))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    shard_weights = [0] * shard_count
    effective_weights: Mapping[str, int] = (
        file_weights if file_weights is not None else dict.fromkeys(ordered_files, 1)
    )
    missing_weights = sorted(set(ordered_files) - set(effective_weights))
    if missing_weights:
        raise RuntimeError("missing test file weights: " + ", ".join(missing_weights))

    # DA-001: narrow ``effective_weights`` from the ``Mapping[str, int] | None``
    # parameter type so the per-shard budget math below infers ``int``,
    # not ``Any``.
    weight_map: dict[str, int] = (
        dict(effective_weights) if file_weights is not None else dict.fromkeys(ordered_files, 1)
    )

    def _file_sort_key(path: str) -> tuple[int, str]:
        return (-weight_map[path], path)

    def _shard_sort_key(index: int) -> tuple[int, int]:
        return (shard_weights[index], index)

    for path in sorted(ordered_files, key=_file_sort_key):
        shard_index = min(range(shard_count), key=_shard_sort_key)
        shards[shard_index].append(path)
        shard_weights[shard_index] += weight_map[path]
    assignment = tuple(tuple(sorted(shard)) for shard in shards)
    validate_exact_file_assignment(ordered_files, assignment)
    return assignment


def validate_exact_file_assignment(
    selected_files: Iterable[str],
    shards: Iterable[Iterable[str]],
) -> None:
    """Fail unless shards contain every selected file exactly once."""
    selected = set(selected_files)
    assigned_paths = tuple(path for shard in shards for path in shard)
    assigned = set(assigned_paths)
    counts = Counter(assigned_paths)
    duplicate_paths = sorted(path for path, count in counts.items() if count > 1)
    missing_paths = sorted(selected - assigned)
    unexpected_paths = sorted(assigned - selected)
    if duplicate_paths or missing_paths or unexpected_paths:
        details = []
        if duplicate_paths:
            details.append("duplicate files: " + ", ".join(duplicate_paths))
        if missing_paths:
            details.append("missing files: " + ", ".join(missing_paths))
        if unexpected_paths:
            details.append("unexpected files: " + ", ".join(unexpected_paths))
        raise RuntimeError("invalid test shard assignment: " + "; ".join(details))


def _pytest_workers() -> str:
    """Return an explicit override or the CPU-capped verified shard profile.

    The auto profile is bounded by ``_PERFORMANCE_CORE_PYTEST_WORKER_CAP = 10``
    on 12-core hosts, and by ``_MAX_PYTEST_WORKERS = 8`` on larger hosts to avoid
    startup contention. Explicit overrides remain capped at ``available_cores - 2``
    to reserve the parent and OS/I/O capacity. The 12-core profile reserves two
    logical cores for orchestration.
    """
    raw = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    available_cores = os.cpu_count() or 2
    worker_cap = (
        _PERFORMANCE_CORE_PYTEST_WORKER_CAP
        if available_cores <= _HETEROGENEOUS_CORE_HOST_MAX_CORES
        else _MAX_PYTEST_WORKERS
    )
    if raw == "auto":
        auto_cores = 1 if available_cores <= _MINIMUM_MULTI_SHARD_CORES else available_cores
        return str(min(worker_cap, auto_cores))
    try:
        requested = int(raw)
    except ValueError:
        return raw
    explicit_max = max(1, available_cores - 2)
    return str(max(1, min(requested, explicit_max)))


def _xdist_workers_per_shard() -> str:
    """Return the in-shard xdist worker count, or ``"0"`` to disable xdist.

    The default is ``"0"`` (plain pytest per shard), which on the maintained
    8-shard CI profile keeps the slowest shard well under the 60-second
    combined budget because the shard-saturated 8-shard fan-out already
    uses one pytest process per shard and adding in-shard xdist workers
    trades parallel IO for pytest-coordination overhead. Operators
    on hosts with idle cores beyond the shard cap (e.g. 64-core CI), or
    hosts with very few shards, may opt into the legacy
    CPU-utilisation-maximising policy by setting
    ``PYTEST_XDIST_WORKERS_PER_SHARD=auto``; ``auto`` then sizes each
    shard's xdist fan-out so that ``PYTEST_WORKERS`` shards * ``N``
    in-shard workers does not exceed the host's available CPU count.
    Setting ``PYTEST_XDIST_WORKERS_PER_SHARD`` to a positive integer
    overrides the policy with an explicit per-shard fan-out; ``0`` opts
    out of in-shard xdist (the default).
    """
    raw = os.getenv("PYTEST_XDIST_WORKERS_PER_SHARD", _DEFAULT_XDIST_WORKERS_PER_SHARD)
    if raw != "auto":
        return raw
    available_cores = os.cpu_count() or 2
    # ``_pytest_workers`` already keeps one core for the runner's
    # deadline/cleanup loop, so the in-shard fan-out is bounded by the
    # remaining cores divided by the shard count. We cap each shard at
    # ``_MAX_XDIST_WORKERS_PER_SHARD`` so a future per-shard bump cannot
    # silently exceed the host's actual parallelism.
    shard_count = max(1, int(_pytest_workers()))
    per_shard = max(1, available_cores // shard_count)
    return str(min(_MAX_XDIST_WORKERS_PER_SHARD, per_shard))


def _default_spawner(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> ShardProcess:
    return _PYTEST_SHARD_PROCESS_MANAGER.spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            env=dict(env),
            stdout=None,
            stderr=None,
            label="verify:pytest-shard",
        ),
    )


def _shard_command(
    *,
    manifest_path: Path,
    basetemp: Path,
    marker_expression: str = _VERIFICATION_MARK_EXPRESSION,
    xdist_workers: str = "0",
) -> tuple[str, ...]:
    """Build the pytest command for one shard.

    DA-003 (wt-028-display P1 / AC-08 / S-13): the shard
    command opts out of pytest-cache writes
    (``-p no:cacheprovider``) and header output
    (``--no-header``) so the per-shard overhead of writing the
    ``tests/.pytest_cache`` directory and emitting the platform/
    rootdir banner is bounded. On the maintained 8-shard fan-out the
    cache-write + banner path adds 100-300 ms of wall time per
    shard; eliminating it on every shard keeps the slowest shard
    well under the combined 60-second budget. The cache is
    still honored for raw targets that deliberately use the default
    xdist path, while maintained shard profiles avoid shared cache writes.

    wt-01-fs-opti S-1: the shard command additionally opts into pytest-xdist
    (``-n N`` + ``--dist loadgroup``) when ``xdist_workers`` is a positive
    integer. The in-shard xdist fan-out shrinks each shard's wall clock
    so the slowest shard fits inside the 60s combined budget enforced
    upstream by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``. The
    ``loadgroup`` scheduler keeps long-running tests on the same worker
    rather than scattering them, which is the right default for the
    maintenance-heavy integration tests. ``xdist_workers == "0"`` opts out
    of xdist (legacy plain-pytest behavior) and is reserved for an
    operator override.
    """
    use_xdist = xdist_workers.isdigit() and int(xdist_workers) > 0
    if use_xdist:
        xdist_args: tuple[str, ...] = (
            "-n",
            xdist_workers,
            "--dist",
            "loadgroup",
        )
    else:
        xdist_args = ()
    return (
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-p",
        "ralph.testing.pytest_shard_manifest_plugin",
        "--ralph-shard-manifest",
        str(manifest_path),
        *xdist_args,
        "-m",
        marker_expression,
        "--basetemp",
        str(basetemp),
        *(("--durations", str(_PYTEST_DURATIONS_REPORT)) if _PYTEST_DURATIONS_REPORT > 0 else ()),
    )


def _is_subprocess_e2e_marker(expression: ast.expr) -> bool:
    """Return whether an expression is the ``pytest.mark.subprocess_e2e`` marker."""
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "subprocess_e2e"
        and isinstance(expression.value, ast.Attribute)
        and expression.value.attr == "mark"
        and isinstance(expression.value.value, ast.Name)
        and expression.value.value.id == "pytest"
    )


def _is_module_subprocess_e2e(source: str, *, filename: str) -> bool:
    """Return whether a module-level ``pytestmark`` marks every item as E2E."""
    if "pytestmark" not in source or "subprocess_e2e" not in source:
        return False
    tree = ast.parse(source, filename=filename)
    final_marker: ast.expr | None = None
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        ):
            final_marker = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and (statement.target.id == "pytestmark")
        ):
            # An annotation without a value or a non-simple later assignment
            # makes static classification uncertain, so retain the module.
            final_marker = None
        elif (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.target, ast.Name)
            and (statement.target.id == "pytestmark")
        ):
            final_marker = None
    if final_marker is None:
        return False
    if _is_subprocess_e2e_marker(final_marker):
        return True
    if not isinstance(final_marker, (ast.List, ast.Tuple, ast.Set)):
        return False
    return any(_is_subprocess_e2e_marker(element) for element in final_marker.elts)


def _discover_all_test_files(cwd: Path) -> tuple[str, ...]:
    """Return all pytest files using pytest's default filename patterns."""
    tests_root = cwd / "tests"
    selected_files = {
        path.relative_to(cwd).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for path in tests_root.rglob(pattern)
        if path.is_file()
    }
    if not selected_files:
        raise RuntimeError(f"static pytest discovery selected no files under {tests_root}")
    return tuple(sorted(selected_files))


def _discover_profile_test_files(cwd: Path, *, integration_only: bool) -> tuple[str, ...]:
    """Return a focused profile's static file selection without pytest collection."""
    integration_prefix = "tests/integration/"
    selected = tuple(
        path
        for path in _discover_all_test_files(cwd)
        if path.startswith(integration_prefix) is integration_only
    )
    if not selected:
        profile_name = _INTEGRATION_PROFILE if integration_only else _UNIT_PROFILE
        raise RuntimeError(f"static {profile_name} pytest discovery selected no files")
    return selected


def discover_unit_test_files(cwd: Path) -> tuple[str, ...]:
    """Return unit files, excluding the integration subtree exactly."""
    return _discover_profile_test_files(cwd, integration_only=False)


def discover_integration_test_files(cwd: Path) -> tuple[str, ...]:
    """Return integration files from the integration subtree exactly."""
    return _discover_profile_test_files(cwd, integration_only=True)


def discover_fast_test_files(cwd: Path) -> tuple[str, ...]:
    """Return the fail-closed static test-fast routing-contract selection."""
    missing_files = tuple(path for path in FAST_PROFILE_TEST_FILES if not (cwd / path).is_file())
    if missing_files:
        raise RuntimeError(f"static fast pytest discovery missing files: {missing_files}")
    return FAST_PROFILE_TEST_FILES


_DISCOVERY_WORKER_CAP: Final = 8
_PARALLEL_DISCOVERY_THRESHOLD: Final = 32
_DISCOVERY_CHUNK_SIZE: Final = 64
_DISCOVERY_FORK_CONTEXT = multiprocessing.get_context("fork")


def _inspect_test_file(
    payload: tuple[str, str, bool],
) -> tuple[str, str, int, bool]:
    cwd_str, path, is_required = payload
    cwd = Path(cwd_str)
    if is_required:
        source = (cwd / path).read_text(encoding="utf-8")
        weight = estimate_test_file_weight(source)
        return path, source, weight, True
    source_bytes = (cwd / path).read_bytes()
    if b"pytestmark" not in source_bytes or b"subprocess_e2e" not in source_bytes:
        source = source_bytes.decode("utf-8")
        weight = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(path) or _fast_test_count(source)
        return path, source, weight, True
    source = source_bytes.decode("utf-8")
    if not _is_module_subprocess_e2e(source, filename=path):
        weight = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(path) or estimate_test_file_weight(source)
        return path, source, weight, True
    return path, source, 0, False


def discover_test_files(cwd: Path) -> tuple[str, ...]:
    """Return verification files, excluding only statically proven E2E-only modules.

    Avoid parsing the common non-E2E case: a module can only be E2E-only when
    it assigns ``pytestmark`` and mentions ``subprocess_e2e``.  This keeps
    discovery below the one-second per-test ceiling while preserving the
    conservative AST classification for every possible E2E candidate.

    ``_FILE_SOURCE_CACHE`` so ``_test_file_weight`` does not re-read the same
    file from disk during shard weight computation. Per-file weights are
    also populated into ``_FILE_WEIGHT_CACHE`` during the same single pass:
    the common non-E2E path uses a lightweight regex over the source to
    count ``test_`` function definitions (parametrize multipliers are not
    material to LPT placement), while files already requiring
    ``ast.parse`` for E2E classification get an exact AST-derived weight
    for free. On a 1300-file tree this saves ~5s of redundant ``ast.parse``
    work in the parent process, which directly lowers the slowest-shard
    wall time under the 60s combined budget. Required auto-integrate E2E
    files also cache their source; the multiplier is applied once by
    ``_test_file_weight`` when the shard plan is built.
    """
    all_files = _discover_all_test_files(cwd)
    req_set = set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    cwd_str = str(cwd)
    payloads = [(cwd_str, path, path in req_set) for path in all_files]
    workers = min(_DISCOVERY_WORKER_CAP, len(payloads), os.cpu_count() or 1)
    if workers <= 1 or len(payloads) < _PARALLEL_DISCOVERY_THRESHOLD:
        results = [_inspect_test_file(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=_DISCOVERY_FORK_CONTEXT) as pool:
            results = list(pool.map(_inspect_test_file, payloads, chunksize=_DISCOVERY_CHUNK_SIZE))

    discovered: list[str] = []
    for path, source, weight, include in results:
        if include:
            _cache_source(path, source)
            _FILE_WEIGHT_CACHE[path] = weight
            discovered.append(path)
    selected = tuple(discovered)
    validate_required_auto_integrate_selection(selected)
    return selected


def discover_subprocess_e2e_files(cwd: Path) -> tuple[str, ...]:
    """Return test files that statically reference the subprocess-E2E marker.

    Supplying explicit files avoids pytest collecting the entire repository
    merely to deselect non-E2E tests. Pytest still applies the canonical marker
    expression within each selected file, preserving function-level exclusions.

    The maintained 1300-file tree previously spent ~7.5 s in this pass
    parsing every file even when most files contain no ``subprocess_e2e``
    marker. A two-stage filter avoids that work:

    1. Skip files that don't even contain the ``subprocess_e2e`` substring
       (cheap ``bytes`` membership test).
    2. Stop walking the AST as soon as one ``pytest.mark.subprocess_e2e``
       reference is found (early exit from ``ast.walk``).
    """
    selected: list[str] = []
    for relative_path in _discover_all_test_files(cwd):
        source_bytes = (cwd / relative_path).read_bytes()
        if b"subprocess_e2e" not in source_bytes:
            continue
        source = source_bytes.decode("utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue
        if _contains_subprocess_e2e(tree):
            selected.append(relative_path)
    if not selected:
        raise RuntimeError("static subprocess-E2E discovery selected no files")
    return tuple(selected)


def _contains_subprocess_e2e(tree: ast.AST) -> bool:
    """Return True iff ``tree`` contains at least one ``pytest.mark.subprocess_e2e`` reference."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "subprocess_e2e"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            return True
    return False


def _literal_parametrize_case_count(decorator: ast.expr) -> int:
    """Return the statically knowable collection multiplier for one decorator."""
    if not isinstance(decorator, ast.Call):
        return 1
    target = decorator.func
    if not (
        isinstance(target, ast.Attribute)
        and target.attr == "parametrize"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "pytest"
    ):
        return 1
    cases: ast.expr | None
    if len(decorator.args) > _PARAMETRIZE_CASES_ARGUMENT_INDEX:
        cases = decorator.args[_PARAMETRIZE_CASES_ARGUMENT_INDEX]
    else:
        cases = next(
            (keyword.value for keyword in decorator.keywords if keyword.arg == "argvalues"),
            None,
        )
    if isinstance(cases, ast.List):
        return max(1, len(cases.elts))
    if isinstance(cases, ast.Tuple):
        return max(1, len(cases.elts))
    if isinstance(cases, ast.Set):
        return max(1, len(cases.elts))
    return 1


def estimate_test_file_weight(source: str) -> int:
    """Estimate collection work from visible tests and literal parametrization.

    Literal ``pytest.mark.parametrize`` case lists represent distinct pytest
    items, so account for them rather than balancing only function definitions.
    Dynamic parameter sources remain conservatively weighted as one item.

    pytest only collects test functions at module scope or as direct methods
    of a class. This walker descends through ``ast.ClassDef`` bodies but
    stops at any function (whether it is a test or not) because pytest does
    not collect test functions nested inside other functions. Skipping
    nested functions and the rest of ``ast.walk`` keeps the walk below
    4.5 ms per file across a 1300-file tree, halving the original
    ``ast.walk``-based budget without changing the reported weight for
    any file in the maintained suite (verified via the equivalence test in
    ``tests/test_test_suites.py::test_estimate_test_file_weight_matches_ast_walk``).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 1
    weight = 0
    body_stack: list[list[ast.stmt]] = [list(tree.body)]
    while body_stack:
        body = body_stack[-1]
        if not body:
            body_stack.pop()
            continue
        node = body.pop()
        if isinstance(node, ast.ClassDef):
            body_stack.append(list(node.body))
            continue
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        multiplier = 1
        for decorator in node.decorator_list:
            multiplier *= _literal_parametrize_case_count(decorator)
        weight += multiplier
    return max(1, weight)


_TEST_DEF_PATTERN = re.compile(r"^(?:\s*)(?:async )?def test_")


def _fast_test_count(source: str) -> int:
    """Count test definitions and literal parametrized collection cases.

    This AST-free estimate is used during static discovery for LPT shard
    placement. A decorator's literal case list is multiplied into the
    immediately following test definition, preventing large parametrized
    modules from being assigned a deceptively small weight.
    """
    if "parametrize" in source:
        return estimate_test_file_weight(source)
    return sum(1 for line in source.splitlines() if _TEST_DEF_PATTERN.match(line))


def _weight_from_test_count(test_count: int) -> int:
    """Return the load weight of a file whose only estimate is its test count.

    The LPT partition compares weights, so a weight must stand for a
    duration. The count-based estimate is therefore scaled into the same
    time units as the measured override table: one weight unit is
    ``_WEIGHT_SECONDS_PER_UNIT`` of wall clock and a counted test is
    assumed to cost ``_BASE_SECONDS_PER_TEST``. Without the scale the
    counted files outweigh the measured heavy ones, which is what left the
    general lane's shards 1.29x apart in wall clock.
    """
    return max(1, round(test_count * _BASE_SECONDS_PER_TEST / _WEIGHT_SECONDS_PER_UNIT))


def _test_file_weight(cwd: Path, relative_path: str) -> int:
    """Return the LPT load weight of one selected test file.

    A measured override wins outright: its value is already a duration in
    weight units. Otherwise the file's collected-test count (cached by the
    discovery pass) is scaled into the same time units.
    """
    override = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(relative_path)
    if override is not None:
        return override
    # Reuse the parser cache populated by ``discover_test_files`` to avoid
    # re-reading and re-parsing the same source files during shard weight
    # computation. Without this cache, each shard load pays the cost of an
    # additional full read and ``ast.parse`` for every selected file even
    # though ``discover_test_files`` already walked the same files.
    test_count = _estimate_weight_from_cache(relative_path)
    if test_count is None:
        source = (cwd / relative_path).read_text(encoding="utf-8")
        test_count = estimate_test_file_weight(source)
    weight = _weight_from_test_count(test_count)
    if relative_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        return weight * _REQUIRED_E2E_WEIGHT_MULTIPLIER
    return weight


_FILE_SOURCE_CACHE: dict[str, str] = {}
_FILE_WEIGHT_CACHE: dict[str, int] = {}


def _cache_source(path: str, source: str) -> None:
    """Store the parsed source for ``path`` so later weight lookups avoid re-reading."""
    _FILE_SOURCE_CACHE[path] = source


def _estimate_weight_from_cache(path: str) -> int | None:
    """Return the cached collected-test count for ``path``, if it is known."""
    cached_source = _FILE_SOURCE_CACHE.get(path)
    if cached_source is None:
        return None
    cached_weight = _FILE_WEIGHT_CACHE.get(path)
    if cached_weight is not None:
        return cached_weight
    weight = estimate_test_file_weight(cached_source)
    _FILE_WEIGHT_CACHE[path] = weight
    return weight


def reset_discovery_cache() -> None:
    """Reset the transient source/weight caches populated during file discovery.

    The runner calls this between separate ``make test`` / ``make
    test-subprocess-e2e`` invocations so a stale cache cannot leak across
    pytest profiles; the in-flight invocation still benefits from the
    cached source the discovery pass already paid to parse.
    """
    _FILE_SOURCE_CACHE.clear()
    _FILE_WEIGHT_CACHE.clear()


def _remaining_seconds(deadline: float, monotonic: Callable[[], float]) -> float:
    return max(0.0, deadline - monotonic())


def _decode_output(output: bytes | None) -> str:
    return output.decode("utf-8", errors="replace") if output else ""


def _reap_process(
    process: ShardProcess,
    *,
    timeout_seconds: float,
) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        process.cleanup_orphans()
        if timeout_seconds <= 0.0:
            return "", ""
        return "", "pytest shard did not exit after termination\n"
    process.cleanup_orphans()
    return _decode_output(stdout), _decode_output(stderr)


def _drain_running_process(
    process: ShardProcess,
    *,
    timeout_seconds: float,
) -> tuple[int, tuple[str, str]] | None:
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        return None
    returncode = process.poll()
    if returncode is None:
        return None
    process.cleanup_orphans()
    return returncode, (_decode_output(stdout), _decode_output(stderr))


def _terminate_and_reap(
    processes: Sequence[ShardProcess],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[tuple[str, str], ...]:
    # Each ``process.terminate`` is wrapped so a raise on one process
    # (e.g. a foreign exception from a real process manager when its
    # descendant accounting trips) cannot skip the rest of the live
    # shard pool. The original failure is observed (a stderr line)
    # while every still-running shard is still reaped; the budget
    # tracker in ``ralph/verify.py`` charges this drain, so
    # short-cutting one terminate call leaks the rest of the
    # process group and inflates wall-clock on a busy host.
    for process in processes:
        if process.poll() is None:
            try:
                process.terminate(grace_period_s=0.0)
            except Exception as exc:
                # Best-effort reap: a foreign raise from a real process
                # manager cannot skip the rest of the live shard pool.
                print(
                    f"warning: shard terminate raised {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
    outputs = []
    for process in processes:
        remaining = min(
            _SHARD_TERMINATION_DRAIN_SECONDS,
            _remaining_seconds(deadline, monotonic),
        )
        outputs.append(_reap_process(process, timeout_seconds=remaining))
    return tuple(outputs)


def _print_shard_outputs(outputs: Sequence[tuple[str, str]]) -> None:
    for stdout, stderr in outputs:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)


def _print_timeout_diagnostics(
    shards: Sequence[Sequence[str]],
    processes: Sequence[ShardProcess],
    *,
    started_at: float,
    monotonic: Callable[[], float],
) -> None:
    """Name each incomplete shard before deadline cleanup loses its pytest output."""
    elapsed_seconds = monotonic() - started_at
    for index, (shard, process) in enumerate(zip(shards[: len(processes)], processes, strict=True)):
        if process.poll() is None:
            print(
                f"pytest shard {index} timed out after {elapsed_seconds:.2f}s; "
                f"files: {', '.join(shard)}",
                file=sys.stderr,
            )


def _finalize_shard_returncode(
    processes: Sequence[ShardProcess],
    *,
    empty_selection_returncode: int | None,
) -> int:
    if empty_selection_returncode is None:
        return 0
    returncodes = tuple(process.poll() for process in processes)
    if returncodes and all(returncode == empty_selection_returncode for returncode in returncodes):
        return empty_selection_returncode
    return 0


def _run_shards(
    shards: Sequence[Sequence[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    basetemp_root: Path,
    deadline: float,
    started_at: float,
    spawner: ShardSpawner,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
    marker_expression: str = _VERIFICATION_MARK_EXPRESSION,
    xdist_workers: str = "0",
    successful_returncodes: frozenset[int] = frozenset((0,)),
    empty_selection_returncode: int | None = None,
) -> int:
    processes: list[ShardProcess] = []
    try:
        for shard_index, shard in enumerate(shards):
            if _remaining_seconds(deadline, monotonic) <= 0:
                _print_timeout_diagnostics(
                    shards, processes, started_at=started_at, monotonic=monotonic
                )
                outputs = _terminate_and_reap(
                    processes,
                    deadline=deadline,
                    monotonic=monotonic,
                )
                _print_shard_outputs(outputs)
                return TIMEOUT_EXIT_CODE
            manifest_path = basetemp_root / f"shard-{shard_index}.manifest"
            manifest_path.write_text(  # filesystem-write-ok: transient per-run shard scratch
                "".join(f"{path}\n" for path in shard),
                encoding="utf-8",
            )
            print(
                f"pytest shard {shard_index}: {len(shard)} files, "
                f"xdist={xdist_workers}, started at {monotonic() - started_at:.2f}s",
                flush=True,
            )
            shard_basetemp = basetemp_root / f"shard-{shard_index}"
            shard_basetemp.mkdir(  # filesystem-write-ok: transient per-run shard basetemp directory
                mode=0o700, parents=True, exist_ok=True
            )
            processes.append(
                spawner(
                    _shard_command(
                        manifest_path=manifest_path,
                        basetemp=shard_basetemp,
                        marker_expression=marker_expression,
                        xdist_workers=xdist_workers,
                    ),
                    cwd=cwd,
                    env=env,
                )
            )
    except OSError as exc:
        outputs = _terminate_and_reap(processes, deadline=deadline, monotonic=monotonic)
        _print_shard_outputs(outputs)
        print(f"failed to start pytest shard: {exc}", file=sys.stderr)
        return 2

    completed: dict[int, tuple[str, str]] = {}
    while len(completed) < len(processes):
        if _remaining_seconds(deadline, monotonic) <= 0:
            _print_timeout_diagnostics(
                shards, processes, started_at=started_at, monotonic=monotonic
            )
            outputs = _terminate_and_reap(
                processes,
                deadline=deadline,
                monotonic=monotonic,
            )
            _print_shard_outputs(outputs)
            return TIMEOUT_EXIT_CODE
        for index, process in enumerate(processes):
            if index in completed:
                continue
            returncode = process.poll()
            if returncode is None:
                drained = _drain_running_process(
                    process,
                    timeout_seconds=min(
                        _SHARD_POLL_INTERVAL_SECONDS,
                        _remaining_seconds(deadline, monotonic),
                    ),
                )
                if drained is None:
                    continue
                returncode, completed[index] = drained
            else:
                completed[index] = _reap_process(
                    process,
                    timeout_seconds=_remaining_seconds(deadline, monotonic),
                )
            print(
                f"pytest shard {index}: exit={returncode}, elapsed={monotonic() - started_at:.2f}s",
                flush=True,
            )
            if returncode not in successful_returncodes:
                siblings = [
                    sibling
                    for sibling_index, sibling in enumerate(processes)
                    if sibling_index not in completed
                ]
                sibling_outputs = _terminate_and_reap(
                    siblings,
                    deadline=deadline,
                    monotonic=monotonic,
                )
                all_outputs = [
                    completed.get(output_index, ("", "")) for output_index in range(len(processes))
                ]
                for sibling_index, output in zip(
                    (
                        candidate
                        for candidate in range(len(processes))
                        if candidate not in completed
                    ),
                    sibling_outputs,
                    strict=True,
                ):
                    all_outputs[sibling_index] = output
                _print_shard_outputs(all_outputs)
                return returncode
        if len(completed) < len(processes):
            wait(min(_SHARD_POLL_INTERVAL_SECONDS, _remaining_seconds(deadline, monotonic)))

    _print_shard_outputs([completed[index] for index in range(len(processes))])
    return _finalize_shard_returncode(
        processes,
        empty_selection_returncode=empty_selection_returncode,
    )


def _select_test_files(
    *,
    cwd: Path,
    file_discoverer: Callable[[Path], tuple[str, ...]],
    profile: str | None,
    auto_integrate_e2e_only: bool,
    subprocess_e2e_only: bool,
) -> tuple[tuple[str, ...], str, tuple[str, ...], int | None]:
    """Resolve one fail-closed profile into static files and shard settings.

    Returns the selected files, the marker expression, the required
    real-git files, and the number of shards the real-git lane is split
    into (``None`` when the lane is empty).
    """
    if profile is not None and profile not in _FOCUSED_PROFILES:
        raise ValueError(f"unknown test-suite profile: {profile}")
    active_selector_count = sum((auto_integrate_e2e_only, subprocess_e2e_only, profile is not None))
    if active_selector_count > 1:
        raise ValueError("test-suite profiles are mutually exclusive")
    if profile == _UNIT_PROFILE:
        return (
            discover_unit_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if profile == _INTEGRATION_PROFILE:
        return (
            discover_integration_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if profile == _FAST_PROFILE:
        return (
            discover_fast_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if auto_integrate_e2e_only:
        return (
            tuple(
                path
                for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES
                if path not in NON_SELECTABLE_REQUIRED_REGISTRY_FILES
            ),
            _VERIFICATION_MARK_EXPRESSION,
            (),
            None,
        )
    if subprocess_e2e_only:
        selected_files = discover_subprocess_e2e_files(cwd)
        validate_exclusive_subprocess_e2e_selection(selected_files)
        exclusive_files = tuple(
            path for path in selected_files if path in set(EXCLUSIVE_SUBPROCESS_E2E_FILES)
        )
        general_files = tuple(
            path for path in selected_files if path not in set(EXCLUSIVE_SUBPROCESS_E2E_FILES)
        )
        return (
            general_files,
            _SUBPROCESS_E2E_MARK_EXPRESSION,
            exclusive_files,
            0,
        )
    discovered_files = file_discoverer(cwd)
    validate_required_auto_integrate_selection(discovered_files)
    required_files = tuple(
        path for path in discovered_files if path in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    selected_files = tuple(
        path for path in discovered_files if path not in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    required_shard_count = _required_e2e_shard_count() if required_files else None
    return (selected_files, _VERIFICATION_MARK_EXPRESSION, required_files, required_shard_count)


def run_test_suites(
    *,
    cwd: Path,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
    spawner: ShardSpawner = _default_spawner,
    file_discoverer: Callable[[Path], tuple[str, ...]] = discover_test_files,
    file_weigher: Callable[[Path, str], int] = _test_file_weight,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
    auto_integrate_e2e_only: bool = False,
    subprocess_e2e_only: bool = False,
    profile: str | None = None,
) -> int:
    """Run the maintained pytest verification suite and return its exit code.

    Args:
        cwd: Working directory for the pytest subprocess (the package
            root that contains ``tests/``).
        suite_timeout_seconds: Wall-clock cap for this single pytest
            invocation. Default ``DEFAULT_SUITE_TIMEOUT_SECONDS``
            (60 s). Note this is a per-invocation cap only — the
            60-second COMBINED budget across every test step is
            enforced upstream by ``ralph.verify`` via cumulative
            ``time.monotonic()`` tracking; the elapsed time of this
            function counts against that budget.
        spawner: Process seam used to start each plain-pytest shard.
        file_discoverer: Static pytest-file discovery seam.
        file_weigher: Static deterministic load weight for each selected file.

    Returns:
        ``0`` on success, the first failing pytest shard's return code, or
        ``124`` when the single parent deadline expires.

    Side effects:
        Spawns concurrent pytest shards via ``spawner``. Each shard
        inherits an environment with
        ``RALPH_PYTEST_TEST_TIMEOUT_SECONDS`` and
        ``RALPH_PYTEST_SUITE_TIMEOUT_SECONDS`` populated.
    """
    started_at = monotonic()
    deadline = started_at + suite_timeout_seconds
    inherited_env = dict(os.environ)
    # An activated external environment can export site-packages through
    # PYTHONPATH. Pytest shards must use the same interpreter selected by
    # ``sys.executable`` rather than importing incompatible binary wheels.
    # The Makefile deliberately exports this package root, however, so retain
    # that exact, single-entry value: it prevents Python from falling back to
    # packages compiled for an unrelated interpreter.
    pythonpath = inherited_env.get("PYTHONPATH")
    if pythonpath is None or Path(pythonpath).resolve() != cwd.resolve():
        inherited_env.pop("PYTHONPATH", None)
    env = build_timeout_env(
        base_env=inherited_env,
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=suite_timeout_seconds,
    )
    (
        selected_files,
        marker_expression,
        required_e2e_shard,
        required_e2e_shard_count,
    ) = _select_test_files(
        cwd=cwd,
        file_discoverer=file_discoverer,
        profile=profile,
        auto_integrate_e2e_only=auto_integrate_e2e_only,
        subprocess_e2e_only=subprocess_e2e_only,
    )

    # These files used to run in a pytest process that had to exit before
    # the parallel fan-out started. That process spent ~25s (full-tree
    # collection plus ~11s of fast tests) on the critical path, so the
    # parallel shards started with too little of the 60s combined budget
    # left and the suite watchdog killed them. The product-baseline bench
    # stays under 1s even under xdist load, so the files join the same
    # LPT partition as every other selected file.
    shards = partition_selected_files(
        selected_files,
        worker_count=int(_pytest_workers()),
        file_weights={path: file_weigher(cwd, path) for path in selected_files},
    )
    # The required real-git files run as a lane of plain-pytest shards that
    # start alongside the general shards. The lane is partitioned by the
    # same measured weights, so its shards carry equal loads; plain shards
    # keep the lane's module imports single instead of one per xdist
    # worker.
    required_e2e_shards: tuple[tuple[str, ...], ...] = ()
    if required_e2e_shard:
        if required_e2e_shard_count and required_e2e_shard_count > 1:
            required_e2e_shards = partition_selected_files(
                required_e2e_shard,
                worker_count=required_e2e_shard_count,
                file_weights={path: file_weigher(cwd, path) for path in required_e2e_shard},
            )
        else:
            required_e2e_shards = (required_e2e_shard,)

    if required_e2e_shards:
        validate_exact_file_assignment(
            (*selected_files, *(path for shard in required_e2e_shards for path in shard)),
            (*shards, *required_e2e_shards),
        )
    else:
        validate_exact_file_assignment(selected_files, shards)
    print(
        f"pytest preparation: {len(selected_files) + len(required_e2e_shard)} files, "
        f"{len(shards) + len(required_e2e_shards)} shards in {monotonic() - started_at:.2f}s",
        flush=True,
    )
    temp_directory_prefix: str
    if profile is not None:
        temp_directory_prefix = profile
    elif auto_integrate_e2e_only:
        temp_directory_prefix = "auto-integrate-e2e"
    elif subprocess_e2e_only:
        temp_directory_prefix = "subprocess-e2e"
    else:
        temp_directory_prefix = "verification"
    basetemp_parent = Path(tempfile.gettempdir()) / "ralph-pytest-shards"
    basetemp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{temp_directory_prefix}-",
        dir=basetemp_parent,
    ) as basetemp_root:
        basetemp_path = Path(basetemp_root)
        successful_returncodes = frozenset((0, 5)) if profile is not None else frozenset((0,))
        empty_selection_returncode = 5 if profile is not None else None
        # The real-git E2E lane runs concurrently with the general shards as
        # plain-pytest shards, so all shards complete inside the 60 s
        # combined budget in ralph/verify.py. The exclusive subprocess_e2e
        # lane continues to sequence after general shards.
        run_required_concurrently = bool(required_e2e_shards and not subprocess_e2e_only)
        all_shards = (*shards, *required_e2e_shards) if run_required_concurrently else shards
        general_result = _run_shards(
            all_shards,
            cwd=cwd,
            env=env,
            basetemp_root=basetemp_path,
            deadline=deadline,
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=_xdist_workers_per_shard(),
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )
        if general_result not in successful_returncodes:
            return general_result
        if not required_e2e_shards or run_required_concurrently:
            return general_result
        # The exclusive subprocess-E2E shard contains eight real-PTY
        # regressions that each spawn a fresh Python child importing the
        # ralf package (~1.5s of import time per child).  Four in-shard
        # xdist workers cut the wall clock from ~13s to ~7s, keeping
        # the combined subprocess profile inside the 60s budget.  The
        # other profiles do not use this fallback (the auto-integrate
        # E2E shard runs concurrently with the general shards via
        # ``run_required_concurrently``).
        if subprocess_e2e_only:
            xdist_workers_for_required = _EXCLUSIVE_SUBPROCESS_E2E_SHARD_XDIST_WORKERS
        else:
            xdist_workers_for_required = _xdist_workers_per_shard()
        return _run_shards(
            (required_e2e_shards[0],),
            cwd=cwd,
            env=env,
            basetemp_root=basetemp_path,
            deadline=deadline,
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=xdist_workers_for_required,
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )


def _run_durations_profile(cwd: Path) -> int:
    """Run each test file individually and print per-file wall-clock durations."""
    inherited_env = dict(os.environ)
    pythonpath = inherited_env.get("PYTHONPATH")
    if pythonpath is None or Path(pythonpath).resolve() != cwd.resolve():
        inherited_env.pop("PYTHONPATH", None)
    env = build_timeout_env(
        base_env=inherited_env,
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=DEFAULT_SUITE_TIMEOUT_SECONDS,
    )
    seen_files: dict[str, None] = {}
    for test_file in (*discover_test_files(cwd), *REQUIRED_AUTO_INTEGRATE_E2E_FILES):
        seen_files[test_file] = None
    files = tuple(seen_files)
    basetemp_parent = Path(tempfile.gettempdir()) / "ralph-pytest-durations"
    basetemp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="durations-", dir=basetemp_parent) as tmp_root:
        for index, relative_path in enumerate(files):
            command = (
                sys.executable,
                "-m",
                "pytest",
                relative_path,
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                "--durations=0",
                "--durations-min=0.0001",
                "--basetemp",
                str(Path(tmp_root) / str(index)),
            )
            file_started_at = time.monotonic()
            process = _default_spawner(command, cwd=cwd, env=env)
            _reap_process(process, timeout_seconds=_DURATIONS_PER_FILE_TIMEOUT_SECONDS)
            print(f"{relative_path}: {time.monotonic() - file_started_at}", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``ralph.test_suites`` command-line tool.

    Forwards to :func:`run_test_suites` using the current working
    directory. Returns the pytest subprocess exit code. Positional
    arguments are rejected with ``SystemExit`` to surface silent
    misuse.
    """
    sanitize_process_environment()
    arguments = tuple(argv or ())
    if arguments == ("--auto-integrate-e2e",):
        return run_test_suites(cwd=Path.cwd(), auto_integrate_e2e_only=True)
    if arguments == ("--subprocess-e2e",):
        return run_test_suites(cwd=Path.cwd(), subprocess_e2e_only=True)
    if arguments == ("--profile", _DURATIONS_PROFILE):
        return _run_durations_profile(cwd=Path.cwd())
    if (
        len(arguments) == _PROFILE_OPTION_ARGUMENT_COUNT
        and arguments[0] == "--profile"
        and arguments[1] in _FOCUSED_PROFILES
    ):
        return run_test_suites(cwd=Path.cwd(), profile=arguments[1])
    if arguments:
        raise SystemExit(
            "ralph.test_suites accepts --profile unit, --profile integration, --profile fast, or --profile durations, "
            "--auto-integrate-e2e, or --subprocess-e2e"
        )
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
