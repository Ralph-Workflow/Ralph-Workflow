"""End-to-end regression: ``reconcile_target_onto_remote`` drives the workspace-context.

The auto-rebase conflict path on the main worktree is the workspace-context
feature's first driver. The shared resolver built by
``build_agent_rebase_stop_resolver`` enters ``workspace_context`` for the
target ``root`` the reconcile module passes in, and the resolver's with-block
covers decline, pipeline exception, success, and abort so the caller is
restored byte-identical. The existing ``TestAutoRebaseWorkspaceContextEndToEnd``
suite (in ``test_auto_integrate_remote_sync_reconcile.py``) drives the
resolver directly; this module drives it through the real reconcile module
so every reconcile boundary is exercised end to end.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


def _seed_workspace(root: Path, *, prompt: str) -> None:
    """Create a minimal Ralph workspace at ``root`` with the given ``prompt``."""
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")


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


def _config(**overrides: object):
    from ralph.config.models import UnifiedConfig

    base: dict[str, dict[str, object]] = {
        "general": {
            "auto_integrate_remote_enabled": True,
            "auto_integrate_remote": "origin",
        },
    }
    base["general"].update(overrides)
    return UnifiedConfig.model_validate(base)


def _load_default_policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_with_chain_agent() -> object:
    """Registry whose chain lookup returns a sentinel agent.

    The real default policy's rebase-conflict-resolution drain resolves
    through ``AgentRegistry.from_config``; the seeded builtins already
    cover every chain name.
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


class TestReconcileTargetOntoRemoteWorkspaceContextEndToEnd:
    """``reconcile_target_onto_remote`` drives the workspace-context path.

    The reconcile module's main-owner rebase stop calls the resolver it
    was passed (``rebase_stop_resolver``). When that resolver is the real
    ``build_agent_rebase_stop_resolver`` built for the calling worktree,
    the resolver must enter ``workspace_context`` for the target owner
    passed in by the reconcile module and observe the target's prompt,
    policy, registry, config, and MCP plan through the pipeline. The
    caller is restored byte-identical regardless of outcome.
    """

    @staticmethod
    def _drive_resolver_through_reconcile(
        monkeypatch: pytest.MonkeyPatch,
        caller: Path,
        target: Path,
        *,
        resolver_outcome: bool,
    ) -> dict[str, object]:
        """Drive ``reconcile_target_onto_remote`` with the real shared resolver.

        Patches every reconcile-module boundary (``_reconciliation_preconditions``,
        ``write_record``, ``rebase_onto``, ``rebase_in_progress``,
        ``resolve_rebase_in_progress``, ``abort_rebase``, ``branch_sha``,
        ``clear_record``) so the path runs without real Git/network effects.
        The fake ``resolve_rebase_in_progress`` invokes the received resolver,
        so the real ``build_agent_rebase_stop_resolver`` runs end to end.

        Returns a dict containing the pipeline observations and the
        ``ReconciliationOutcome`` so the caller can assert both the
        target-context assertions and the snapshot equality.
        """
        from ralph.git.rebase.rebase import RebaseConflicts
        from ralph.pipeline import auto_integrate_agent as resolver_module
        from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

        owner = target

        # Reconcile preconditions short-circuit: pretend the caller is the
        # main worktree's repo root and the target is the owning target
        # worktree. None of the seams below touch real Git.
        monkeypatch.setattr(
            remote_reconcile,
            "_reconciliation_preconditions",
            lambda *_a, **_kw: (owner, "before_sha", None),
        )
        monkeypatch.setattr(remote_reconcile, "write_record", lambda *_a, **_kw: None)
        monkeypatch.setattr(
            remote_reconcile,
            "rebase_onto",
            lambda *_a, **_kw: RebaseConflicts("conflict"),
        )
        # ``rebase_in_progress`` drives four probes in the path:
        # (1) initial "still rebasing?" check, (2) post-resolver "did the
        # resolver finish?" check, (3) and (4) the two probes inside
        # ``_abort_restore_or_retain_record`` that gate the abort and the
        # "retained for recovery" return. Pick the sequence based on the
        # desired outcome.
        if resolver_outcome:
            # Success: resolver returned True, the second probe reads False,
            # the path returns the clean-reconciliation outcome without
            # invoking the abort probe again.
            rebase_states: Iterator[bool] = iter((True, False))
        else:
            # Decline / abort: resolver returned False; every subsequent
            # probe must read True (so ``abort_rebase`` fires) except the
            # final probe inside ``_abort_restore_or_retain_record`` which
            # must read False (so the call lands on the cleanly-aborted
            # outcome rather than the "retained for recovery" branch).
            rebase_states = iter((True, True, True, False))
        monkeypatch.setattr(
            remote_reconcile,
            "rebase_in_progress",
            lambda *_a, **_kw: next(rebase_states, False),
        )

        observations: dict[str, object] = {}
        resolver_call_args: list[tuple[Path, str, RebaseStop]] = []

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
            return resolver_outcome

        monkeypatch.setattr(
            resolver_module,
            "run_rebase_conflict_resolution_pipeline",
            _record_pipeline,
        )

        def _invoke_resolver(root: Path, target_ref: str, received: object) -> bool:
            stop = RebaseStop(
                sha="abc1234",
                subject="feat: alpha",
                conflicted_files=("src/alpha.py",),
                stop_index=1,
                stop_cap=10,
            )
            resolver_call_args.append((root, target_ref, stop))
            return received(root, target_ref, stop)

        monkeypatch.setattr(remote_reconcile, "resolve_rebase_in_progress", _invoke_resolver)
        # ``abort_rebase`` is intentionally left unpatched here so the
        # calling test can record its invocations; the helper only stubs
        # the seams every path uses. ``branch_sha`` and ``clear_record``
        # are stubbed because the test doesn't need to observe them.
        monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a, **_kw: "before_sha")
        monkeypatch.setattr(remote_reconcile, "clear_record", lambda *_a, **_kw: None)

        resolver = resolver_module.build_agent_rebase_stop_resolver(
            policy_bundle=_load_default_policy_bundle(),
            registry=_registry_with_chain_agent(),
            display=MagicMock(),
            config=_config(),
            pipeline_deps=object(),
            workspace_scope=object(),
        )

        outcome = remote_reconcile.reconcile_target_onto_remote(
            caller,
            "main",
            "origin",
            rebase_stop_resolver=resolver,
        )

        return {
            "observations": observations,
            "resolver_call_args": resolver_call_args,
            "outcome": outcome,
        }

    def test_reconcile_target_onto_remote_drives_shared_resolver_with_target_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """End-to-end: ``reconcile_target_onto_remote`` threads the target context.

        The shared resolver built by ``build_agent_rebase_stop_resolver``
        enters ``workspace_context`` for the conflicted owner that the
        reconcile module passes in. The pipeline must receive the
        TARGET's prompt bytes, scope, and policy, and the caller's
        resources must be byte-identical before and after the call.
        """
        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        _seed_workspace(caller, prompt="CALLER PROMPT")
        _seed_workspace(target, prompt="TARGET PROMPT")
        _install_workspace_seams(monkeypatch, caller, target)

        before = _snapshot_caller(caller)
        result = self._drive_resolver_through_reconcile(
            monkeypatch,
            caller,
            target,
            resolver_outcome=True,
        )
        after = _snapshot_caller(caller)

        observations = result["observations"]
        assert observations["root"] == target
        assert observations["workspace_scope"].root == target.resolve()
        assert observations["prompt_bytes"] == (target / "PROMPT.md").read_bytes()
        assert observations["prompt_bytes"] != (caller / "PROMPT.md").read_bytes()
        # The reconcile module invoked the shared resolver once with the
        # target owner root and the rebased-onto branch.
        assert len(result["resolver_call_args"]) == 1
        called_root, called_target, _stop = result["resolver_call_args"][0]
        assert called_root == target
        assert called_target == "origin/main"
        # The reconcile outcome succeeded -- the resolver returned True and
        # the second ``rebase_in_progress`` check was False.
        assert result["outcome"].reconciled is True
        # Caller's observable resources are byte-identical before and after.
        assert after == before

    def test_reconcile_target_onto_remote_aborts_leave_caller_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A resolver decline aborts the rebase without touching the caller.

        When the resolver returns False the reconcile module aborts the
        target rebase and the caller is left untouched. The caller
        snapshot before the call matches the caller snapshot after.
        """
        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        _seed_workspace(caller, prompt="CALLER PROMPT")
        _seed_workspace(target, prompt="TARGET PROMPT")
        _install_workspace_seams(monkeypatch, caller, target)

        abort_calls: list[bool] = []

        def _record_abort(_root: object) -> None:
            abort_calls.append(True)

        monkeypatch.setattr(
            remote_reconcile,
            "abort_rebase_discarding_progress",
            _record_abort,
        )

        before = _snapshot_caller(caller)
        result = self._drive_resolver_through_reconcile(
            monkeypatch,
            caller,
            target,
            resolver_outcome=False,
        )
        after = _snapshot_caller(caller)

        assert len(result["resolver_call_args"]) == 1
        assert result["outcome"].reconciled is False
        assert result["outcome"].cleanly_aborted is True
        # abort_rebase fires once in the main branch (after the resolver
        # declined) and once inside ``_abort_restore_or_retain_record``
        # before the clean-abort outcome is returned. Both are idempotent.
        assert abort_calls == [True, True]
        # The caller is byte-identical before and after.
        assert after == before
