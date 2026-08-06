"""Configured-remote synchronization defaults and explicit opt-out coverage."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.general_config import GeneralConfig
from ralph.pipeline.auto_integrate_remote_sync import (
    pull_and_reconcile_target,
    push_target_after_landing,
    remote_sync_enabled,
    remote_target_name,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest


def _config(**overrides: object) -> GeneralConfig:
    return GeneralConfig.model_validate(overrides)


def test_remote_sync_default_on_fetches_and_pushes_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """S-2: default configuration enables configured-remote operations."""
    calls: list[str] = []
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.refresh_target_from_remote",
        lambda *_args, **_kwargs: calls.append("fetch") or "local fleet",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._remote_push_module.push_branch_to_single_remote",
        lambda *_args, **_kwargs: calls.append("push") or "pushed main to origin",
    )

    config = _config()
    record = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    assert remote_sync_enabled(config) is True
    assert remote_target_name(config) == "origin"
    assert pull_and_reconcile_target(config, Path("/repo"), "main") is None
    assert push_target_after_landing(config, Path("/repo"), "main", record).last_push is not None
    assert calls == ["fetch", "push"]


def test_remote_sync_explicit_false_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """S-2: explicit false remains the operator-controlled local-only opt-out."""
    calls: list[str] = []
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.refresh_target_from_remote",
        lambda *_args, **_kwargs: calls.append("fetch"),
    )
    config = _config(auto_integrate_remote_enabled=False)
    assert remote_sync_enabled(config) is False
    assert pull_and_reconcile_target(config, Path("/repo"), "main") is None
    assert calls == []
