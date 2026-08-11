"""Behavior-only contract for the :func:`workspace_context` context manager.

The context manager must expose a target worktree's full context (the
active prompt, config, policy, agent registry, and MCP plan) and leave
the caller's resources byte-identical after every exit path: normal
exit, nested entry, exception, and explicit early exit. A missing or
invalid target raises **before** yielding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.workspace.scope import resolve_workspace_scope


class TestWorkspaceContext:
    """Behavior-only contract for :func:`workspace_context`."""

    @staticmethod
    def _seed_workspace(root: Path, *, prompt: str) -> None:
        """Create the minimal .agent structure for one workspace."""
        agent_dir = root / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
        (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")

    @staticmethod
    def _install_workspace_seams(
        monkeypatch: pytest.MonkeyPatch,
        *workspace_roots: Path,
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

    @staticmethod
    def _caller_snapshot(root: Path) -> dict[str, object]:
        """Snapshot the observable parts of the caller's context."""
        from ralph.agents.registry import AgentRegistry
        from ralph.config.loader import load_config
        from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
        from ralph.policy.loader import load_policy_for_workspace_scope

        scope = resolve_workspace_scope(root)
        config = load_config(workspace_scope=scope)
        policy = load_policy_for_workspace_scope(scope, config=config)
        registry = AgentRegistry.from_config(config)
        mcp_plan = resolve_effective_session_mcp_plan(scope.root)
        return {
            "prompt_bytes": (scope.root / "PROMPT.md").read_bytes(),
            "config": config,
            "policy": policy,
            "registry": registry,
            "mcp_plan": mcp_plan,
            "scope_root": scope.root,
        }

    def test_yields_target_scope_config_policy_registry_prompt_and_mcp_plan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The bundle exposes the target's complete observed context."""
        from ralph.workspace.context import WorkspaceContext, workspace_context

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        with workspace_context(target) as ctx:
            assert isinstance(ctx, WorkspaceContext)
            assert ctx.target_scope.root == target.resolve()
            assert (ctx.target_scope.root / "PROMPT.md").read_text(encoding="utf-8") == "TARGET PROMPT"
            assert ctx.config is not None
            assert ctx.policy_bundle is not None
            assert ctx.registry is not None
            assert ctx.effective_mcp_plan is not None
            assert ctx.target_scope.root == ctx.target_scope.root

    def test_target_values_differ_from_caller_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The bundle is the TARGET's, not the caller's."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        before = self._caller_snapshot(caller)
        with workspace_context(target) as ctx:
            assert ctx.target_scope.root != before["scope_root"]
            target_prompt_bytes = (target / "PROMPT.md").read_bytes()
            assert target_prompt_bytes != before["prompt_bytes"]

    def test_caller_snapshot_unchanged_on_normal_exit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A successful round trip returns the caller to its prior state."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        before = self._caller_snapshot(caller)
        with workspace_context(target):
            pass
        after = self._caller_snapshot(caller)

        assert after["prompt_bytes"] == before["prompt_bytes"]
        assert after["config"] == before["config"]
        assert after["policy"] == before["policy"]
        assert after["scope_root"] == before["scope_root"]

    def test_caller_snapshot_unchanged_when_inner_block_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An exception inside `with` does not mutate the caller's snapshot."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        before = self._caller_snapshot(caller)
        with pytest.raises(RuntimeError, match="boom"), workspace_context(target):
            raise RuntimeError("boom")
        after = self._caller_snapshot(caller)

        assert after["prompt_bytes"] == before["prompt_bytes"]
        assert after["config"] == before["config"]
        assert after["policy"] == before["policy"]
        assert after["scope_root"] == before["scope_root"]

    def test_caller_snapshot_unchanged_on_nested_round_trip(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Nested use discards the inner bundle without mutating the outer."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        caller.mkdir()
        outer.mkdir()
        inner.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(outer, prompt="OUTER PROMPT")
        self._seed_workspace(inner, prompt="INNER PROMPT")
        self._install_workspace_seams(monkeypatch, caller, outer, inner)

        before = self._caller_snapshot(caller)
        with workspace_context(outer) as outer_ctx:
            assert outer_ctx.target_scope.root == outer.resolve()
            with workspace_context(inner) as inner_ctx:
                assert inner_ctx.target_scope.root == inner.resolve()
            assert outer_ctx.target_scope.root == outer.resolve()
        after = self._caller_snapshot(caller)

        assert after["prompt_bytes"] == before["prompt_bytes"]
        assert after["config"] == before["config"]
        assert after["policy"] == before["policy"]
        assert after["scope_root"] == before["scope_root"]

    def test_explicit_early_exit_leaves_caller_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A `return` inside the `with` block restores the caller cleanly."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        target = tmp_path / "target"
        caller.mkdir()
        target.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._seed_workspace(target, prompt="TARGET PROMPT")
        self._install_workspace_seams(monkeypatch, caller, target)

        def _use_target() -> str:
            with workspace_context(target) as ctx:
                return ctx.target_scope.root.as_posix()

        before = self._caller_snapshot(caller)
        result = _use_target()
        after = self._caller_snapshot(caller)

        assert result == target.resolve().as_posix()
        assert after["prompt_bytes"] == before["prompt_bytes"]
        assert after["config"] == before["config"]
        assert after["policy"] == before["policy"]
        assert after["scope_root"] == before["scope_root"]

    def test_missing_target_raises_before_yielding(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A non-existent target raises a clear error and never yields."""
        from ralph.workspace.context import workspace_context

        caller = tmp_path / "caller"
        caller.mkdir()
        self._seed_workspace(caller, prompt="CALLER PROMPT")
        self._install_workspace_seams(monkeypatch, caller)

        missing = tmp_path / "does-not-exist"
        with pytest.raises((ValueError, FileNotFoundError)), workspace_context(missing):
            pytest.fail("workspace_context must not yield for a missing target")
