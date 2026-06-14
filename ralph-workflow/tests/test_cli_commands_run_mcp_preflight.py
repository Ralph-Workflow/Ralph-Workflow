"""Tests for custom MCP validation in run-command preflight."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.cli.commands import run as run_module
from ralph.cli.commands._execute_pipeline_request import _ExecutePipelineRequest
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry

if TYPE_CHECKING:
    import pytest

    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


def _fake_preflight_request(
    *,
    config: UnifiedConfig,
    workspace_scope: object,
    policy_bundle: object,
    initial_state: object,
    counter_overrides: dict[str, int],
    inline_prompt: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        initial_state=initial_state,
        counter_overrides=counter_overrides,
        inline_prompt=inline_prompt,
    )


class _FakeWorkspaceScope:
    def __init__(self, root: str) -> None:
        self.root = Path(root)


def test_run_preflight_aborts_when_custom_mcp_validation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[Path] = []

    def fake_validate(workspace_root: Path) -> int:
        called.append(workspace_root)
        return 1

    monkeypatch.setattr(run_module, "validate_custom_mcp_servers", fake_validate)
    monkeypatch.setattr(run_module, "validate_required_inputs", lambda _scope: None)
    (tmp_path / "PROMPT.md").write_text("prompt", encoding="utf-8")

    result = run_module._run_preflight_checks(
        _fake_preflight_request(
            config=UnifiedConfig(),
            workspace_scope=cast("WorkspaceScope", _FakeWorkspaceScope(str(tmp_path))),
            policy_bundle=None,
            initial_state=None,
            counter_overrides={},
            inline_prompt=None,
        ),
        display_context=make_display_context(),
    )

    assert result == 2
    assert called == [tmp_path]


def test_run_pipeline_stops_before_execution_when_custom_mcp_validation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []

    def fake_validate(workspace_root: Path) -> int:
        calls.append(workspace_root)
        return 1

    monkeypatch.setattr(run_module, "validate_custom_mcp_servers", fake_validate)
    monkeypatch.setattr(run_module, "validate_required_inputs", lambda _scope: None)
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: UnifiedConfig())
    monkeypatch.setattr(run_module, "load_policy_for_workspace_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_module, "_get_run_func", lambda: lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        run_module,
        "resolve_workspace_scope",
        lambda: cast("WorkspaceScope", _FakeWorkspaceScope(str(tmp_path))),
    )
    (tmp_path / "PROMPT.md").write_text("prompt", encoding="utf-8")

    result = run_module.run_pipeline(display_context=make_display_context())

    assert result == 2
    assert calls == [tmp_path]


def test_execute_pipeline_forwards_pro_hooks_model_identity_and_policy_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_execute_pipeline`` builds PipelineDeps through the shared factory and
    forwards Pro hooks, model identity, and the loaded policy bundle into the
    runner so the main pipeline uses the same initialization path as plumbing.
    """
    captured: dict[str, object] = {}

    def fake_runner(
        _config: UnifiedConfig,
        _initial_state: object,
        *,
        pipeline_deps: object,
        pro_hooks: object,
        **kwargs: object,
    ) -> int:
        captured["kwargs"] = {"pipeline_deps": pipeline_deps, "pro_hooks": pro_hooks, **kwargs}
        return 0

    monkeypatch.setattr(run_module, "_get_run_func", lambda: fake_runner)

    display_context = make_display_context()
    pro_hooks = ProPipelineHooks(snapshot_registry=SnapshotRegistry())
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    policy_bundle = object()
    request = _ExecutePipelineRequest(
        config=UnifiedConfig(),
        initial_state=None,
        policy_bundle=cast("PolicyBundle | None", policy_bundle),
        verbosity=None,
        counter_overrides={},
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )

    result = run_module._execute_pipeline(request, display_context=display_context)

    assert result == 0
    kwargs = cast("dict[str, object]", captured["kwargs"])
    pipeline_deps = cast("PipelineDeps", kwargs["pipeline_deps"])
    assert pipeline_deps.policy_bundle is policy_bundle
    assert pipeline_deps.model_identity is model_identity
    assert pipeline_deps.snapshot_registry is pro_hooks.snapshot_registry
    assert kwargs["pro_hooks"] is pro_hooks
