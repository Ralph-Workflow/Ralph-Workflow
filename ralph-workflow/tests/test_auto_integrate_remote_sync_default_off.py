"""AC-13 regression guard: with ``auto_integrate_remote_sync_enabled`` off, no remote sync runs.

The opt-in remote sync tier is OFF by default. When the flag is unset
(default) OR explicitly ``false``, the run must be byte-identical to a
run on a previous version: no ``git fetch``, no ``git push``, and the
existing observe-only ``git fetch origin <target>`` probe (gated by
``auto_integrate_fetch_enabled``) is the ONLY allowed network call.
A strictly-ahead ``origin/<target>`` is reported as
``REFRESH_ORIGIN_AHEAD`` and the local ref is left untouched (the local
ref remains the authoritative mainline pointer for every local rebase
and landing).

These tests pin the contract from outside the auto_integrate module
through the existing ``refresh_target_from_remote`` and
``push_branch_to_single_remote`` helpers, exercising both the
configuration default path and an explicit false override. ``real_git
or monkeypatch -- never sleep`` is enforced by the
``audit_test_policy`` contract.
"""

from __future__ import annotations

from pathlib import Path as _Path
from typing import TYPE_CHECKING

import loguru

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import (
    _maybe_imply_fetch_enabled,
    _warn_deprecated_push_enabled,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    pull_and_reconcile_target,
    push_target_after_landing,
    remote_backoff_max_seconds,
    remote_sync_enabled,
    remote_sync_interval_seconds,
    remote_target_name,
    remote_wait_seconds,
    wait_for_remote_publish,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ORIGIN_AHEAD,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    import pytest


def _build_config(**overrides: object) -> GeneralConfig:
    """Construct a ``GeneralConfig`` with the given overrides only.

    Defaults come from the model; absence is the unchanged baseline
    and a constructed config with no overrides is the AC-13
    byte-identical baseline.
    """
    return GeneralConfig.model_validate(overrides or {})


def test_remote_sync_default_off_returns_no_remote_action() -> None:
    """The default config never enables remote sync.

    Pins the AC-13 default-off contract: with the field unset the
    gate returns ``False`` and every helper short-circuits to its
    observe-only / no-op behaviour.
    """
    config = _build_config()
    assert remote_sync_enabled(config) is False
    assert remote_target_name(config) == "origin"
    assert remote_sync_interval_seconds(config) == 300.0
    assert remote_backoff_max_seconds(config) == 300.0
    assert remote_wait_seconds(config) == 0.0


def test_remote_sync_explicit_false_returns_no_remote_action() -> None:
    """Explicit ``false`` is byte-identical to unset."""
    config = _build_config(auto_integrate_remote_sync_enabled=False)
    assert remote_sync_enabled(config) is False


def test_loader_does_not_imply_fetch_when_remote_sync_disabled() -> None:
    """``auto_integrate_fetch_enabled`` is NOT touched by the loader when remote sync is off.

    The implication lives behind a flag ON check; an explicit ``false``
    on the dependent side is preserved.
    """
    data: dict[str, object] = {"auto_integrate_remote_sync_enabled": False}
    _maybe_imply_fetch_enabled(data)
    assert "auto_integrate_fetch_enabled" not in data


def test_pull_and_reconcile_target_is_a_noop_when_remote_sync_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pull-side helper must NOT call refresh / fetch / CAS helpers with the flag off.

    Pins the byte-identical AC-13 contract: ``pull_and_reconcile_target``
    short-circuits to ``None`` before touching any remote or local ref
    and the refresh helpers MUST NOT be reached.
    """
    calls: list[tuple[str, object]] = []

    def _refresh_recorder(
        *args: object, **kwargs: object
    ) -> str:
        calls.append(("refresh_target_from_remote", (args, kwargs)))
        return REFRESH_ORIGIN_AHEAD

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.refresh_target_from_remote",
        _refresh_recorder,
    )
    config = _build_config()
    outcome = pull_and_reconcile_target(config, _Path("/repo"), "main")
    assert outcome is None
    assert calls == []


def test_push_target_after_landing_is_a_noop_when_remote_sync_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push-side helper must NOT call any push helper with the flag off.

    Deprecation warning is NOT a remote action; the helper returns the
    record byte-identically without invoking
    ``push_branch_to_single_remote``.
    """
    calls: list[tuple[str, object]] = []

    def _push_recorder(
        *args: object, **kwargs: object
    ) -> str:
        calls.append(("push_branch_to_single_remote", (args, kwargs)))
        return "pushed main to origin"

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync._remote_push_module.push_branch_to_single_remote",
        _push_recorder,
    )

    record = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    config = _build_config()
    returned = push_target_after_landing(config, _Path("/repo"), "main", record)
    assert returned is record
    assert calls == []


def test_wait_for_remote_publish_is_a_noop_when_remote_sync_off() -> None:
    """End-of-run waiting is OFF by default; 0-second budget means no call.

    Pins the AC-13 / AC-40 contract: with the flag off (and even with
    a positive wait when the flag is off) the helper returns
    ``(False, "")`` without contacting any remote.
    """
    config = _build_config(auto_integrate_remote_wait_seconds=120.0)
    published, summary = wait_for_remote_publish(config, _Path("/repo"), "main")
    assert published is False
    assert summary == ""


def test_deprecation_warning_names_the_replacement_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ``auto_integrate_push_enabled = true`` emits a deprecation warning.

    Pins AC-46 / S-2: the deprecation string names the new flag so an
    operator running the legacy config can find the replacement.
    """
    recorded: list[str] = []

    def _intercept(message: str) -> None:
        recorded.append(str(message))

    monkeypatch.setattr(
        loguru.logger, "warning", _intercept, raising=False
    )
    data: dict[str, object] = {"auto_integrate_push_enabled": True}
    _warn_deprecated_push_enabled(data)
    joined = " ".join(recorded)
    assert "auto_integrate_push_enabled" in joined
    assert "auto_integrate_remote_sync_enabled" in joined
