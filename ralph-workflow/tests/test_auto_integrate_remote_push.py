"""Deterministic coverage for opt-in auto-integration target publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_ff
from ralph.pipeline.rebase_state import RebaseState

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _config(*, enabled: bool, timeout: float = 3.0) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_push_enabled": enabled,
                "auto_integrate_push_timeout_seconds": timeout,
                "auto_integrate_remote_target": "origin",
            }
        }
    )


def test_successful_landing_records_configured_remote_push_for_normal_and_recovery_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared hook records the one configured remote after either landing."""
    calls: list[tuple[Path, str, str, float]] = []

    def push(repo: Path, branch: str, *, remote: str, timeout_seconds: float) -> str:
        calls.append((repo, branch, remote, timeout_seconds))
        return "pushed release to origin"

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._remote_push_module.push_branch_to_single_remote",
        push,
    )
    state = RebaseState(last_action="rebased", last_target="release", fast_forwarded=True)

    normal = auto_integrate_ff.maybe_push_target(
        _config(enabled=True), Path("/repo"), "release", state
    )
    recovery = auto_integrate_ff.maybe_push_target(
        _config(enabled=True), Path("/repo"), "release", state
    )

    assert normal.last_push == "pushed release to origin"
    assert recovery.last_push == "pushed release to origin"
    assert calls == [
        (Path("/repo"), "release", "origin", 3.0),
        (Path("/repo"), "release", "origin", 3.0),
    ]


def test_disabled_push_preserves_landing_without_contacting_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default-off hook leaves the local-success record unchanged."""

    def unexpected_push(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("disabled push contacted a remote")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._remote_push_module.push_branch_to_single_remote",
        unexpected_push,
    )
    state = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)

    assert (
        auto_integrate_ff.maybe_push_target(_config(enabled=False), Path("/repo"), "main", state)
        is state
    )
