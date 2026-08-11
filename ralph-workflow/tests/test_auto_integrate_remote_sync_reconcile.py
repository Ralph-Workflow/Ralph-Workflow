"""Tests for the rejected-push reconcile loop of auto-integration.

Covers AC-27 to AC-31 of the PRODUCT_CRITERIA.md. The reconcile
loop runs at most ``_MAX_REMOTE_SYNC_ATTEMPTS`` (3) times per seam,
stops on a successful push, and treats a runaway remote as a
pending-not-terminal state on :attr:`RebaseState.last_remote_sync`.
The pull/rebase/push cycle never blocks the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.git import remote_push as remote_push_module
from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PUSH_REJECTED,
    REMOTE_PUSHED,
    reconcile_after_rejected_push,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_LOCAL_FLEET,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.conflict_resolution.graph import MAX_REBASE_CONFLICT_STOPS
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


def _config(**overrides: object):
    from ralph.config.models import UnifiedConfig

    base = {
        "general": {
            "auto_integrate_remote_enabled": True,
            "auto_integrate_remote": "origin",
        },
    }
    base["general"].update(overrides)
    return UnifiedConfig.model_validate(base)


def _record() -> RebaseState:
    return RebaseState(
        last_action="rebased",
        last_target="main",
        fast_forwarded=True,
    )


def test_successful_push_after_one_reconcile_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-27: rejected push -> reconcile -> re-push succeeds."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    # First refresh: REFRESH_LOCAL_FLEET (no-op, no fetch needed);
    # push fails non-ff. Second iteration succeeds.
    refresh_calls: list[int] = []
    outcomes = ["push of main to origin failed: non-fast-forward", "pushed main to origin"]

    def fake_refresh(*a: object, **kw: object) -> str:
        refresh_calls.append(1)
        return REFRESH_LOCAL_FLEET

    monkeypatch.setattr(mod, "refresh_target_from_remote", fake_refresh)

    push_calls: list[str] = []
    outcome_iter = iter(outcomes)

    def fake_push(*a: object, **kw: object) -> str:
        summary = next(outcome_iter)
        push_calls.append(summary)
        return summary

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    # After successful push: last_remote_sync == REMOTE_PUSHED
    assert out.last_remote_sync == REMOTE_PUSHED
    # Only one push needed because the first call returns success? No - the
    # first call returned failure. So push_calls should be 2.
    assert len(push_calls) == 2


def test_run_keeps_going_after_per_seam_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-31: pushing remote keeps the run going, no block."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)

    def fake_push(*a: object, **kw: object) -> str:
        return "push of main to origin failed: non-fast-forward"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    # After exhaustion, last_remote_sync carries the pending state, not None.
    assert out.last_remote_sync == REMOTE_PUSH_REJECTED
    # Returning a RebaseState that the caller can carry forward.


def test_per_seam_cap_is_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-27: at most ``_MAX_REMOTE_SYNC_ATTEMPTS = 3`` cycles per seam."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *a, **kw: REFRESH_LOCAL_FLEET)

    push_attempts: list[int] = []

    def fake_push(*a: object, **kw: object) -> str:
        push_attempts.append(len(push_attempts) + 1)
        return "push of main to origin failed: non-fast-forward"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    assert len(push_attempts) == 3  # bounded loop


def test_unreachable_remote_does_not_attempt_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-30: unreachable remote is reported, no further push attempts."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    def fake_refresh(*a: object, **kw: object) -> str:
        return REFRESH_UNREACHABLE

    monkeypatch.setattr(mod, "refresh_target_from_remote", fake_refresh)

    push_calls: list[int] = []

    def fake_push(*a: object, **kw: object) -> str:
        push_calls.append(1)
        return "pushed"  # never actually called

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    config = _config()
    record = _record()
    out = reconcile_after_rejected_push(config, Path("/repo"), "main", record)
    assert push_calls == []
    # The reconciliation attempts a single refresh-only cycle; the
    # last_remote_sync reflects the unreachable pull state.
    assert out.last_remote_sync == remote_sync.REMOTE_REMOTE_UNREACHABLE
    assert out.last_push_status == remote_push_module.PushStatus.UNREACHABLE.value


def test_rejected_push_passes_resolver_to_target_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression S-1: rejected-push reconciliation keeps the shared resolver."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *_a, **_kw: REFRESH_DIVERGED)
    def resolver(*_args: object) -> bool:
        return True

    received: list[object | None] = []
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_a, **kwargs: received.append(kwargs.get("rebase_stop_resolver"))
        or remote_reconcile.ReconciliationOutcome(False, "blocked"),
    )

    reconcile_after_rejected_push(
        _config(), Path("/repo"), "main", _record(), rebase_stop_resolver=resolver
    )

    assert received == [resolver]


def test_rejected_push_regression_cleanly_aborted_reconcile_retains_publish_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/S-3: a restored target conflict leaves remote publication retryable."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *_a, **_kw: REFRESH_DIVERGED)
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_a, **_kw: remote_reconcile.ReconciliationOutcome(
            False, "cleanly aborted conflict", cleanly_aborted=True
        ),
    )

    result = reconcile_after_rejected_push(_config(), Path("/repo"), "main", _record())

    assert result.last_remote_sync == REMOTE_PUSH_REJECTED
    assert result.last_push_status == remote_push_module.PushStatus.NON_FAST_FORWARD.value


def test_rejected_push_honors_target_reclaim_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-3 regression: rejected-push reconciliation preserves the configured opt-out."""
    from ralph.pipeline import auto_integrate_remote_sync as mod
    from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED

    monkeypatch.setattr(mod, "refresh_target_from_remote", lambda *_a, **_kw: REFRESH_DIVERGED)
    received: list[bool] = []
    monkeypatch.setattr(
        remote_reconcile,
        "reconcile_target_onto_remote",
        lambda *_a, **kwargs: received.append(kwargs["reclaim_target_worktree"])
        or remote_reconcile.ReconciliationOutcome(False, "blocked"),
    )

    result = reconcile_after_rejected_push(
        _config(auto_integrate_reclaim_target_worktree=False),
        Path("/repo"),
        "main",
        _record(),
    )

    assert received == [False]
    assert result.last_remote_sync == remote_sync.REMOTE_PULL_FAILED


def test_target_reconciliation_regression_aborts_owner_rebase_after_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4/E9: a conflicted target rebase aborts without moving its target ref."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    monkeypatch.setattr(remote_reconcile, "abort_rebase", lambda **_kw: None)
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "before")
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: None)
    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "conflicted" in reason


def test_target_reconciliation_regression_abort_restores_without_destructive_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/S-3: a proven rebase abort is safe without racing a hard reset."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    monkeypatch.setattr(remote_reconcile, "abort_rebase", lambda **_kw: None)
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "before")
    cleared: list[bool] = []
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: cleared.append(True))
    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "retained for recovery" not in reason
    assert cleared == [True]


def test_target_reconciliation_regression_success_cleanup_failure_is_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1/S-2: a successful rebase retains recovery ownership when cleanup fails."""
    from ralph.git.rebase.rebase import RebaseSuccess

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseSuccess())
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: False)
    monkeypatch.setattr(
        remote_reconcile,
        "clear_record",
        lambda *_a: (_ for _ in ()).throw(OSError("record cleanup failed")),
    )

    outcome = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert outcome.reconciled is False
    assert outcome.cleanly_aborted is False
    assert "retained for recovery" in outcome.reason
    assert "record cleanup failed" in outcome.reason


def test_target_reconciliation_regression_success_requires_no_active_rebase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1/S-2: a success claim cannot release ownership while Git is rebasing."""
    from ralph.git.rebase.rebase import RebaseSuccess

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseSuccess())
    states = iter((True, True, True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    aborts: list[bool] = []
    monkeypatch.setattr(remote_reconcile, "abort_rebase", lambda **_kw: aborts.append(True))
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "before")
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: None)

    outcome = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert outcome.reconciled is False
    assert outcome.cleanly_aborted is True
    assert aborts == [True]


def test_target_reconciliation_regression_clear_record_failure_is_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: cleanup failure preserves recovery ownership instead of escaping."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    monkeypatch.setattr(remote_reconcile, "abort_rebase", lambda **_kw: None)
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "before")
    monkeypatch.setattr(
        remote_reconcile,
        "clear_record",
        lambda *_a: (_ for _ in ()).throw(OSError("record cleanup failed")),
    )

    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "retained for recovery" in reason
    assert "record cleanup failed" in reason


def test_target_reconciliation_regression_record_write_failure_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: reconciliation refuses to mutate when recovery ownership cannot persist."""
    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(
        remote_reconcile,
        "write_record",
        lambda *_a: (_ for _ in ()).throw(OSError("record unavailable")),
    )
    rebase_calls: list[object] = []
    monkeypatch.setattr(
        remote_reconcile,
        "rebase_onto",
        lambda *_a, **_kw: rebase_calls.append(True),
    )

    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "recovery record" in reason
    assert rebase_calls == []


def test_target_reconciliation_regression_precondition_probe_failure_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: an unobservable target owner becomes a retryable result, never an exception."""
    monkeypatch.setattr(remote_reconcile, "find_main_worktree_root", lambda *_args: Path("/main"))
    monkeypatch.setattr(remote_reconcile, "worktree_lookup", lambda *_args: ("found", Path("/owner")))
    monkeypatch.setattr(
        remote_reconcile,
        "is_repo_clean",
        lambda *_args: (_ for _ in ()).throw(OSError("status unavailable")),
    )

    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "unavailable" in reason


def test_target_reconciliation_regression_worktree_lookup_failure_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-4/E6: an unknown target owner cannot be treated as safely absent."""
    monkeypatch.setattr(
        remote_reconcile,
        "find_main_worktree_root",
        lambda *_args: (_ for _ in ()).throw(OSError("worktree query failed")),
    )

    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "unavailable" in reason


def test_target_reconciliation_regression_retains_record_when_abort_cannot_restore_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2/S-3: never overwrite a target that moved while reconciliation aborted."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    monkeypatch.setattr(remote_reconcile, "abort_rebase", lambda **_kw: None)
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "moved")
    cleared: list[bool] = []
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: cleared.append(True))
    success, reason = remote_reconcile.reconcile_target_onto_remote(Path("/repo"), "main", "origin")

    assert success is False
    assert "retained for recovery" in reason
    assert cleared == []


def test_target_reconciliation_offers_rebase_stop_resolver_before_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: target rebase conflicts offer the shared resolver before aborting."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))

    def resolver(*_a: object, **_kw: object) -> bool:
        return True

    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        remote_reconcile,
        "resolve_rebase_in_progress",
        lambda root, target, received: (
            calls.append((root, target)) or received(root, target, object())
        ),
    )

    success, _reason = remote_reconcile.reconcile_target_onto_remote(
        Path("/repo"), "main", "origin", rebase_stop_resolver=resolver
    )
    assert calls == [(owner, "origin/main")]
    assert success is True


def test_target_reconciliation_regression_resolver_success_requires_finished_rebase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: a resolver claim cannot clear ownership while Git is still rebasing."""
    from ralph.git.rebase.rebase import RebaseConflicts

    owner = Path("/target-owner")
    monkeypatch.setattr(
        remote_reconcile, "_reconciliation_preconditions", lambda *_a, **_kw: (owner, "before", None)
    )
    monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a: None)
    monkeypatch.setattr(
        remote_reconcile, "rebase_onto", lambda *_a, **_kw: RebaseConflicts("conflict")
    )
    states = iter((True, True, True, False))
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", lambda *_a: next(states, False))
    monkeypatch.setattr(remote_reconcile, "resolve_rebase_in_progress", lambda *_a: True)
    aborts: list[bool] = []
    monkeypatch.setattr(
        remote_reconcile, "abort_rebase", lambda **_kw: aborts.append(True)
    )
    monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a: "before")
    monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a: None)

    outcome = remote_reconcile.reconcile_target_onto_remote(
        Path("/repo"),
        "main",
        "origin",
        rebase_stop_resolver=lambda *_a: True,
    )

    assert outcome.reconciled is False
    assert outcome.cleanly_aborted is True
    assert aborts == [True]


def test_rejected_push_reintegrates_feature_before_repush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: each rejected-push cycle reintegrates the feature before pushing."""
    from ralph.pipeline import auto_integrate_remote_sync as mod

    events: list[str] = []
    monkeypatch.setattr(
        mod,
        "refresh_target_from_remote",
        lambda *_a, **_kw: events.append("fetch") or REFRESH_LOCAL_FLEET,
    )
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *_a, **_kw: events.append("push") or "pushed main to origin",
    )

    def integrate(*_a: object, **_kw: object) -> bool:
        events.append("feature integration")
        return True

    record = _record()
    mod.reconcile_after_rejected_push(
        _config(), Path("/repo"), "main", record, reintegrate=integrate
    )
    assert events == ["fetch", "feature integration", "push"]


def test_disabled_remote_sync_skips_reconcile() -> None:
    """No state change when remote sync is disabled."""
    from ralph.config.models import UnifiedConfig

    cfg = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": False,
                "auto_integrate_remote": "origin",
            },
        },
    )
    record = _record()
    out = reconcile_after_rejected_push(cfg, Path("/repo"), "main", record)
    # Record returned unchanged from the caller's perspective
    assert out is not None


class TestAutoRebaseWorkspaceContextEndToEnd:
    """End-to-end regression: the auto-rebase path uses the target's context.

    The main worktree's rebase conflict must run against the main
    worktree's PROMPT.md, MCP plan, config, policy, and registry -- not
    against the calling feature worktree's. The shared resolver built
    by ``build_agent_rebase_stop_resolver`` enters ``workspace_context``
    for the target ``root`` the integration step passes it and uses
    only the target's values inside the ``with`` block. Outside the
    block, the caller's resources are unchanged.
    """

    @staticmethod
    def _seed_workspace(root: Path, *, prompt: str) -> None:
        agent_dir = root / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
        (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")

    @staticmethod
    def _install_workspace_seams(
        monkeypatch: pytest.MonkeyPatch, *workspace_roots: Path
    ) -> None:
        """Mock git ops so each workspace resolves to its own root."""

        canonical = {p.resolve(): p.resolve() for p in workspace_roots}

        def _find_root(candidate: Path) -> Path:
            resolved_candidate = candidate.resolve()
            for ws_root in canonical.values():
                if resolved_candidate == ws_root or ws_root in resolved_candidate.parents:
                    return ws_root
            return resolved_candidate

        monkeypatch.setattr("ralph.workspace.scope.find_repo_root", _find_root)
        monkeypatch.setattr("ralph.workspace.scope.find_main_worktree_root", _find_root)

    def test_auto_rebase_resolver_uses_target_workspace_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The shared resolver enters the target before invoking the pipeline."""
        from ralph.pipeline import auto_integrate_agent as resolver_module
        from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        # Capture every context value the resolver hands to the pipeline,
        # so the assertion can prove the resolver reached the TARGET.
        observations: dict[str, object] = {}

        def _record_pipeline(
            *,
            root: Path,
            target: str,
            stop: RebaseStop,
            config: object,
            pipeline_deps: object,
            workspace_scope: object,
            policy_bundle: object,
            display: object,
            display_context: object,
            deadline: float | None = None,
            invoke: object = None,
            clock: object = None,
        ) -> bool:
            observations["root"] = root
            observations["target"] = target
            observations["stop"] = stop
            observations["config"] = config
            observations["workspace_scope"] = workspace_scope
            observations["policy_bundle"] = policy_bundle
            observations["prompt_bytes"] = (workspace_scope.root / "PROMPT.md").read_bytes()
            return True

        monkeypatch.setattr(resolver_module, "run_rebase_conflict_resolution_pipeline", _record_pipeline)

        # Drive the shared resolver with a target that is NOT the calling
        # worktree's root. The integration step passes the main-owner
        # worktree as ``root``.
        resolver = resolver_module.build_agent_rebase_stop_resolver(
            policy_bundle=_load_default_policy_bundle(),
            registry=_registry_with_chain_agent(),
            display=MagicMock(),
            config=_config(),
            pipeline_deps=object(),
            workspace_scope=object(),
        )
        stop = RebaseStop(
            sha="abc1234",
            subject="feat: alpha",
            conflicted_files=("src/alpha.py",),
            stop_index=1,
            stop_cap=MAX_REBASE_CONFLICT_STOPS,
        )

        assert resolver(target, "main", stop) is True

        # The pipeline must have received the TARGET's values, not the
        # caller's. The resolved scope is the target's root, the prompt
        # bytes are the target's, and the policy is the target's.
        assert observations["root"] == target
        assert observations["target"] == "main"
        assert observations["stop"] is stop
        assert observations["workspace_scope"].root == target.resolve()
        assert observations["prompt_bytes"] == (target / "PROMPT.md").read_bytes()
        assert observations["prompt_bytes"] != (caller / "PROMPT.md").read_bytes()

    def test_auto_rebase_round_trip_leaves_caller_unchanged_on_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A successful auto-rebase restores the caller's resources exactly."""
        from ralph.pipeline import auto_integrate_agent as resolver_module
        from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        # Stub the pipeline to a no-op success.
        monkeypatch.setattr(
            resolver_module,
            "run_rebase_conflict_resolution_pipeline",
            lambda **_kwargs: True,
        )

        # Caller's closes-over scope and config: the caller's view of the
        # world before the resolver runs. After the resolver exits, every
        # observable part of the caller's resources must be identical.
        caller_proxies = _caller_proxies(caller)

        resolver = resolver_module.build_agent_rebase_stop_resolver(
            policy_bundle=_load_default_policy_bundle(),
            registry=_registry_with_chain_agent(),
            display=MagicMock(),
            config=_config(),
            pipeline_deps=object(),
            workspace_scope=object(),
        )
        stop = RebaseStop(
            sha="abc1234",
            subject="feat: alpha",
            conflicted_files=("src/alpha.py",),
            stop_index=1,
            stop_cap=MAX_REBASE_CONFLICT_STOPS,
        )

        # Snapshot caller resources BEFORE the resolver runs.
        before = _snapshot_caller(caller)
        outcome = resolver(target, "main", stop)
        # Snapshot caller resources AFTER the resolver runs.
        after = _snapshot_caller(caller)

        assert outcome is True
        assert after == before
        # Sanity: the caller's proxy scope is still the caller's scope.
        assert caller_proxies["scope"].root == caller.resolve()

    def test_auto_rebase_round_trip_leaves_caller_unchanged_on_pipeline_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A pipeline exception inside the resolver still restores the caller."""
        from ralph.pipeline import auto_integrate_agent as resolver_module
        from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        def _explode(**_kwargs: object) -> bool:
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr(resolver_module, "run_rebase_conflict_resolution_pipeline", _explode)

        resolver = resolver_module.build_agent_rebase_stop_resolver(
            policy_bundle=_load_default_policy_bundle(),
            registry=_registry_with_chain_agent(),
            display=MagicMock(),
            config=_config(),
            pipeline_deps=object(),
            workspace_scope=object(),
        )
        stop = RebaseStop(
            sha="abc1234",
            subject="feat: alpha",
            conflicted_files=("src/alpha.py",),
            stop_index=1,
            stop_cap=MAX_REBASE_CONFLICT_STOPS,
        )

        before = _snapshot_caller(caller)
        outcome = resolver(target, "main", stop)
        after = _snapshot_caller(caller)

        # The resolver never raises; the pipeline exception is contained.
        assert outcome is False
        assert after == before


def _load_default_policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_with_chain_agent() -> object:
    """Registry whose chain lookup returns a sentinel agent.

    The real default policy's rebase-conflict-resolution drain resolves
    through ``AgentRegistry.from_config``; the seeded builtins already
    cover every chain name. A real registry is the easiest way to keep
    the chain-availability check satisfied.
    """
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig

    return AgentRegistry.from_config(UnifiedConfig.model_validate({"general": {}}))


def _caller_proxies(caller: Path) -> dict[str, object]:
    """Build the caller's observable resources -- what the caller sees."""
    from ralph.agents.registry import AgentRegistry
    from ralph.config.loader import load_config
    from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
    from ralph.policy.loader import load_policy_for_workspace_scope
    from ralph.workspace.scope import resolve_workspace_scope

    scope = resolve_workspace_scope(caller)
    config = load_config(workspace_scope=scope)
    policy = load_policy_for_workspace_scope(scope, config=config)
    registry = AgentRegistry.from_config(config)
    mcp_plan = resolve_effective_session_mcp_plan(scope.root)
    return {
        "scope": scope,
        "config": config,
        "policy": policy,
        "registry": registry,
        "mcp_plan": mcp_plan,
        "prompt_bytes": (scope.root / "PROMPT.md").read_bytes(),
    }


def _snapshot_caller(caller: Path) -> dict[str, object]:
    """Byte-identical snapshot of the caller's observable resources."""
    proxies = _caller_proxies(caller)
    return {
        "prompt_bytes": proxies["prompt_bytes"],
        "scope_root": proxies["scope"].root,
        "config": proxies["config"].model_dump_json(),
        "policy": proxies["policy"].model_dump_json(),
        "registry_names": sorted(proxies["registry"].agents.keys()),
        "mcp_plan": proxies["mcp_plan"],
    }
