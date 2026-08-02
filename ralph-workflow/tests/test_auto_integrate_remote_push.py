"""Deterministic wrapper coverage for opt-in auto-integration publication."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_ff
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest


def _config(*, enabled: bool) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": "origin",
            }
        }
    )


def _record() -> RebaseState:
    return RebaseState(last_action="rebased", last_target="release", fast_forwarded=True)


def test_enabled_remote_sync_delegates_landing_to_push_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public landing wrapper forwards its configured target to the push seam."""
    calls: list[tuple[UnifiedConfig | None, Path, str, RebaseState]] = []
    record = _record()

    def push_hook(
        config: UnifiedConfig | None, repo_root: Path, target: str, received: RebaseState
    ) -> RebaseState:
        calls.append((config, repo_root, target, received))
        return received.model_copy(update={"last_push": "pushed release to origin"})

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_target_after_landing", push_hook
    )

    result = auto_integrate_ff.maybe_push_target(_config(enabled=True), Path("/repo"), "release", record)

    assert result.last_push == "pushed release to origin"
    assert calls == [(_config(enabled=True), Path("/repo"), "release", record)]


def test_disabled_remote_sync_preserves_landing_without_calling_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public wrapper leaves a local landing untouched when sync is disabled."""
    record = _record()

    def unexpected_hook(*_args: object, **_kwargs: object) -> RebaseState:
        raise AssertionError("disabled remote sync called its push hook")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_target_after_landing", unexpected_hook
    )

    assert auto_integrate_ff.maybe_push_target(_config(enabled=False), Path("/repo"), "release", record) is record


def test_push_hook_exception_preserves_local_landing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected remote-hook failure never rolls back a successful local landing."""
    record = _record()

    def failing_hook(*_args: object, **_kwargs: object) -> RebaseState:
        raise RuntimeError("remote unavailable")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_target_after_landing", failing_hook
    )

    assert auto_integrate_ff.maybe_push_target(_config(enabled=True), Path("/repo"), "release", record) is record
