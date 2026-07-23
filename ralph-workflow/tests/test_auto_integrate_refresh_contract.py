"""Deterministic contracts for auto-integration refresh propagation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.pipeline import auto_integrate, auto_integrate_refresh
from ralph.pipeline.auto_integrate_context import record_refresh, record_when_stale
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_ORIGIN,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

_ROOT = Path("/workspace")
_TARGET = "main"


def _build_config(*, fetch_enabled: bool = True) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_fetch_enabled": fetch_enabled,
            }
        }
    )


def _inject_integration_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial_refresh: str = REFRESH_ALREADY_CURRENT,
    result: RebaseState | None = None,
) -> list[str]:
    """Inject context and integration runners, returning observed events."""
    events: list[str] = []
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (_ROOT, "feature", _TARGET, initial_refresh),
    )
    monkeypatch.setattr(
        auto_integrate,
        "_auto_integrate_check_skip_conditions",
        lambda _root, _branch, _target: None,
    )
    monkeypatch.setattr(
        auto_integrate,
        "observe_conflict_identity",
        lambda _root, _target: "feature:main",
    )
    monkeypatch.setattr(
        auto_integrate,
        "resolver_allowed",
        lambda _state, _target, _identity: True,
    )

    def _run(*_args: object, **kwargs: object) -> tuple[RebaseState, bool]:
        events.append(f"integrate:{kwargs['refresh']}")
        return (
            result
            or RebaseState(
                last_action="rebased",
                last_target=_TARGET,
                fast_forwarded=True,
                last_refresh=initial_refresh,
            ),
            False,
        )

    monkeypatch.setattr(auto_integrate, "_integrate_once", _run)
    return events


def test_auto_integrate_passes_freshness_to_the_landing_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _inject_integration_runner(
        monkeypatch, initial_refresh=REFRESH_UNREACHABLE
    )

    outcome = auto_integrate.auto_integrate_after_commit(
        _build_config(), WorkspaceScope(_ROOT), RebaseState()
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert events == [f"integrate:{REFRESH_UNREACHABLE}"]


def test_retry_refreshes_before_running_the_next_landing_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _inject_integration_runner(monkeypatch)
    attempts = iter((True, False))

    def _run(*_args: object, **kwargs: object) -> tuple[RebaseState, bool]:
        events.append(f"integrate:{kwargs['refresh']}")
        return (
            RebaseState(last_action="rebased", last_target=_TARGET),
            next(attempts),
        )

    monkeypatch.setattr(auto_integrate, "_integrate_once", _run)
    monkeypatch.setattr(
        auto_integrate,
        "_refresh_target",
        lambda _config, _root, _target: (
            events.append("refresh") or REFRESH_LOCAL_FLEET
        ),
    )

    auto_integrate.auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(_ROOT),
        RebaseState(),
        sleep=lambda _seconds: events.append("wait"),
        jitter=lambda: 0.0,
    )

    assert events == [
        f"integrate:{REFRESH_ALREADY_CURRENT}",
        "wait",
        "refresh",
        f"integrate:{REFRESH_LOCAL_FLEET}",
    ]


def test_fetch_disabled_reobserves_the_local_target_without_remote_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auto_integrate_refresh,
        "observe_target_sha",
        lambda _root, _target: "local-sha",
    )
    monkeypatch.setattr(
        auto_integrate_refresh,
        "refresh_target_from_remote",
        lambda *_args, **_kwargs: pytest.fail("fetch-disabled path contacted origin"),
    )

    assert (
        auto_integrate_refresh.refresh_target(
            _build_config(fetch_enabled=False), _ROOT, _TARGET
        )
        == REFRESH_LOCAL_FLEET
    )


@pytest.mark.parametrize(
    "refresh",
    [REFRESH_UNREACHABLE, REFRESH_NO_ORIGIN, REFRESH_SUPPRESSED],
)
def test_unhealthy_no_op_refresh_is_recorded(refresh: str) -> None:
    skip = RebaseState(
        last_action="skipped",
        last_reason="no commits beyond target",
        last_target=_TARGET,
    )

    outcome = record_when_stale(skip, refresh)

    assert outcome is not None
    assert outcome.last_refresh == refresh


def test_healthy_no_op_refresh_stays_silent() -> None:
    skip = RebaseState(last_action="skipped", last_target=_TARGET)
    assert record_when_stale(skip, REFRESH_ALREADY_CURRENT) is None


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        ("conflict", "rebase conflict"),
        ("conflict", "rebase conflict followed by merge attempt exception"),
        ("conflict", "conflict resolution failed; merge aborted"),
    ],
)
def test_terminal_records_retain_the_refresh_outcome(
    action: str,
    reason: str,
) -> None:
    outcome = record_refresh(
        RebaseState(last_action=action, last_reason=reason, last_target=_TARGET),
        REFRESH_UNREACHABLE,
    )

    assert outcome.last_action == action
    assert outcome.last_reason == reason
    assert outcome.last_refresh == REFRESH_UNREACHABLE


def test_unreachable_refresh_is_rendered_for_the_operator() -> None:
    message = format_auto_integrate_message(
        "rebased",
        _TARGET,
        None,
        fast_forwarded=True,
        refresh=REFRESH_UNREACHABLE,
    )
    assert REFRESH_UNREACHABLE in message
