"""Tests for custom MCP validation in run-command preflight."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context

if TYPE_CHECKING:
    import pytest

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
