from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.policy.models import PolicyBundle


def _seed_workspace(root: Path, *, prompt: str) -> None:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")


def _install_workspace_seams(monkeypatch: pytest.MonkeyPatch, *workspace_roots: Path) -> None:

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
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_with_chain_agent() -> object:
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig

    return AgentRegistry.from_config(UnifiedConfig.model_validate({"general": {}}))


def _caller_proxies(caller: Path) -> dict[str, object]:
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
    @staticmethod
    def _drive_resolver_through_reconcile(
        monkeypatch: pytest.MonkeyPatch,
        caller: Path,
        target: Path,
        *,
        resolver_outcome: bool,
    ) -> dict[str, object]:
        from ralph.git.rebase.rebase import RebaseConflicts
        from ralph.pipeline import auto_integrate_agent as resolver_module
        from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

        owner = target
        records: list[tuple[Path, IntegrationRecord]] = []
        cleared: list[Path] = []

        def locate_owner(_root: Path, _target: str) -> Path:
            return owner

        def capture_record(root: Path, record: IntegrationRecord) -> None:
            records.append((root, record))

        def rebase_identity(_root: Path) -> tuple[str, str]:
            return "before_sha", "onto_sha"

        monkeypatch.setattr(remote_reconcile, "_reconciliation_owner", locate_owner)
        monkeypatch.setattr(remote_reconcile, "current_rebase_identity", rebase_identity)

        monkeypatch.setattr(
            remote_reconcile,
            "_reconciliation_preconditions",
            lambda *_a, **_kw: (owner, "before_sha", None),
        )
        monkeypatch.setattr(remote_reconcile, "write_record", capture_record)
        monkeypatch.setattr(
            remote_reconcile,
            "rebase_onto",
            lambda *_a, **_kw: RebaseConflicts("conflict"),
        )
        active_rebase = [True]

        def is_rebasing(_root: Path) -> bool:
            return active_rebase[0]

        monkeypatch.setattr(remote_reconcile, "rebase_in_progress", is_rebasing)

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
            assert records[-1][0] == caller
            assert records[-1][1].resolving_rebase
            resolved = received(root, target_ref, stop)
            if resolved:
                active_rebase[0] = False
            return resolved

        monkeypatch.setattr(remote_reconcile, "resolve_rebase_in_progress", _invoke_resolver)
        monkeypatch.setattr(remote_reconcile, "branch_sha", lambda *_a, **_kw: "before_sha")
        monkeypatch.setattr(remote_reconcile, "get_head_sha", lambda *_a: "completed_feature_sha")
        monkeypatch.setattr(remote_reconcile, "clear_record", cleared.append)

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
            "records": records,
            "cleared": cleared,
            "rebase_active": active_rebase[0],
        }

    @pytest.mark.timeout_seconds(5)
    def test_reconcile_target_onto_remote_drives_shared_resolver_with_target_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
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
        assert len(result["resolver_call_args"]) == 1
        called_root, called_target, _stop = result["resolver_call_args"][0]
        assert called_root == target
        assert called_target == "origin/main"
        assert result["outcome"].reconciled is True
        assert result["cleared"] == [caller]
        assert result["rebase_active"] is False
        assert after == before

    def test_reconcile_target_onto_remote_decline_retains_owner_and_caller_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
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
        assert result["outcome"].cleanly_aborted is False
        assert "retained for recovery" in result["outcome"].reason
        assert abort_calls == []
        assert result["cleared"] == []
        assert result["rebase_active"] is True
        owner_root, retained = result["records"][-1]
        assert owner_root == caller
        assert retained.resolving_rebase and retained.owning_worktree == str(target)
        assert after == before
