"""Tests for config propagation across nested workspace scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import InvokeOptions, policy_from_options
from ralph.config.loader import GLOBAL_CONFIG_PATH, load_config
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_MAIN_WAITING_INTERVAL = 45.0
_CHILD_WAITING_INTERVAL = 90.0
_CUSTOM_WAITING_INTERVAL = 60.0
_CUSTOM_SUSPECT_THRESHOLD = 120.0
_IDLE_TIMEOUT = 300.0
_CUSTOM_NO_PROGRESS_CEILING = 300.0


def test_load_config_uses_main_worktree_as_propagation_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_MAIN_WAITING_INTERVAL}\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    child_worktree.mkdir()

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(
        workspace_scope=WorkspaceScope(
            root=child_worktree,
            local_config_path=child_worktree / ".agent" / "ralph-workflow.toml",
            propagated_config_paths=(main_repo / ".agent" / "ralph-workflow.toml",),
        )
    )

    assert config.general.agent_waiting_status_interval_seconds == _MAIN_WAITING_INTERVAL


def test_load_config_prefers_child_worktree_local_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_MAIN_WAITING_INTERVAL}\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    child_worktree.mkdir()
    (child_worktree / ".agent").mkdir()
    (child_worktree / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_CHILD_WAITING_INTERVAL}\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(
        workspace_scope=WorkspaceScope(
            root=child_worktree,
            local_config_path=child_worktree / ".agent" / "ralph-workflow.toml",
            propagated_config_paths=(main_repo / ".agent" / "ralph-workflow.toml",),
        )
    )

    assert config.general.agent_waiting_status_interval_seconds == _CHILD_WAITING_INTERVAL


def test_waiting_status_interval_propagates_to_timeout_policy() -> None:
    """Non-default waiting_status_interval_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        waiting_status_interval_seconds=_CUSTOM_WAITING_INTERVAL,
    )
    policy = policy_from_options(opts)

    assert policy.waiting_status_interval_seconds == _CUSTOM_WAITING_INTERVAL


def test_suspect_threshold_propagates_to_timeout_policy() -> None:
    """Non-default suspect_waiting_on_child_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        suspect_waiting_on_child_seconds=_CUSTOM_SUSPECT_THRESHOLD,
    )
    policy = policy_from_options(opts)

    assert policy.suspect_waiting_on_child_seconds == _CUSTOM_SUSPECT_THRESHOLD


_NO_PROGRESS_CEILING = 600.0


def test_no_progress_ceiling_uses_default_when_not_set() -> None:
    """When max_waiting_on_child_no_progress_seconds is not set, TimeoutPolicy uses the default.

    Regression test for wt-97-timeout: the no-progress ceiling (600s) should be used
    when not explicitly set, so that stale child evidence triggers the shorter
    no-progress ceiling instead of the full waiting ceiling.
    """
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        # max_waiting_on_child_no_progress_seconds not set -> defaults to None in InvokeOptions
    )
    policy = policy_from_options(opts)

    # With idle_timeout_seconds=300, max_waiting_on_child_seconds defaults to 1800.
    # max_waiting_on_child_no_progress_seconds should default to 600 (600 <= 1800).
    assert policy.max_waiting_on_child_no_progress_seconds == _NO_PROGRESS_CEILING


def test_no_progress_ceiling_propagates_explicit_value() -> None:
    """Explicit max_waiting_on_child_no_progress_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        max_waiting_on_child_no_progress_seconds=_CUSTOM_NO_PROGRESS_CEILING,
    )
    policy = policy_from_options(opts)

    assert policy.max_waiting_on_child_no_progress_seconds == _CUSTOM_NO_PROGRESS_CEILING


_CUSTOM_ACTIVITY_TTL = 45.0


def test_activity_evidence_ttl_propagates_explicit_value() -> None:
    """Explicit activity_evidence_ttl_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        activity_evidence_ttl_seconds=_CUSTOM_ACTIVITY_TTL,
    )
    policy = policy_from_options(opts)

    assert policy.activity_evidence_ttl_seconds == _CUSTOM_ACTIVITY_TTL


def test_activity_evidence_ttl_uses_default_when_not_set() -> None:
    """When activity_evidence_ttl_seconds is not set, TimeoutPolicy uses the default 30.0s."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        # activity_evidence_ttl_seconds not set -> defaults to None in InvokeOptions
    )
    policy = policy_from_options(opts)

    assert policy.activity_evidence_ttl_seconds == 30.0


def test_activity_evidence_ttl_zero_disables_feature() -> None:
    """activity_evidence_ttl_seconds=0.0 reaches TimeoutPolicy as 0.0 (legacy disable)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        activity_evidence_ttl_seconds=0.0,
    )
    policy = policy_from_options(opts)

    assert policy.activity_evidence_ttl_seconds == 0.0


# ---------------------------------------------------------------------------
# OS-descendant-only and probe propagation
# ---------------------------------------------------------------------------

_OS_DESCENDANT_ONLY_CEILING = 300.0
_OS_DESCENDANT_ONLY_SUSPECT = 60.0
_CPU_IDLE = 60.0
_LOG_GROWTH = 30.0
_CUSTOM_OS_DESCENDANT_ONLY_CEILING = 90.0
_CUSTOM_CPU_IDLE = 45.0
_CUSTOM_LOG_GROWTH = 15.0


def test_os_descendant_only_ceiling_propagates_explicit_value() -> None:
    """Explicit os_descendant_only_ceiling_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        os_descendant_only_ceiling_seconds=_CUSTOM_OS_DESCENDANT_ONLY_CEILING,
    )
    policy = policy_from_options(opts)
    assert policy.os_descendant_only_ceiling_seconds == _CUSTOM_OS_DESCENDANT_ONLY_CEILING


def test_os_descendant_only_ceiling_uses_default_when_not_set() -> None:
    """When not set, TimeoutPolicy uses OS_DESCENDANT_ONLY_CEILING_SECONDS (300.0, feature enabled).

    The bare TimeoutPolicy dataclass uses OS_DESCENDANT_ONLY_CEILING_SECONDS (300.0)
    as the field default, so omitted fields fall through to the module default.

    Note: the default was raised from 120.0 to 300.0 in the wt-012 work to stop
    the dumb-kill regression at cumulative=159s, idle_elapsed=120s. The classifier
    gate now defers any non-absolute fire while a live subagent is registered, so
    the higher ceiling is safe (it tolerates the typical 95th-percentile sub-step
    latency but does not let a wedged-but-alive agent run forever).
    """
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
    )
    policy = policy_from_options(opts)
    assert policy.os_descendant_only_ceiling_seconds == _OS_DESCENDANT_ONLY_CEILING


def test_os_descendant_only_ceiling_none_is_preserved() -> None:
    """os_descendant_only_ceiling_seconds=None is preserved as None (operator opt-out)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        os_descendant_only_ceiling_seconds=None,
    )
    policy = policy_from_options(opts)
    assert policy.os_descendant_only_ceiling_seconds is None


def test_os_descendant_only_suspect_propagates_explicit_value() -> None:
    """Explicit os_descendant_only_suspect_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        os_descendant_only_suspect_seconds=_OS_DESCENDANT_ONLY_SUSPECT,
    )
    policy = policy_from_options(opts)
    assert policy.os_descendant_only_suspect_seconds == _OS_DESCENDANT_ONLY_SUSPECT


def test_os_descendant_only_suspect_uses_default_when_not_set() -> None:
    """When not set, TimeoutPolicy uses OS_DESCENDANT_ONLY_SUSPECT_SECONDS (60.0)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
    )
    policy = policy_from_options(opts)
    assert policy.os_descendant_only_suspect_seconds == _OS_DESCENDANT_ONLY_SUSPECT


def test_cpu_idle_propagates_explicit_value() -> None:
    """Explicit cpu_idle_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        cpu_idle_seconds=_CUSTOM_CPU_IDLE,
    )
    policy = policy_from_options(opts)
    assert policy.cpu_idle_seconds == _CUSTOM_CPU_IDLE


def test_cpu_idle_uses_default_when_not_set() -> None:
    """When cpu_idle_seconds is not set, TimeoutPolicy uses CPU_IDLE_SECONDS (60.0)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
    )
    policy = policy_from_options(opts)
    assert policy.cpu_idle_seconds == _CPU_IDLE


def test_cpu_idle_none_is_preserved() -> None:
    """cpu_idle_seconds=None is preserved as None (operator opt-out)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        cpu_idle_seconds=None,
    )
    policy = policy_from_options(opts)
    assert policy.cpu_idle_seconds is None


def test_log_growth_propagates_explicit_value() -> None:
    """Explicit log_growth_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        log_growth_seconds=_CUSTOM_LOG_GROWTH,
    )
    policy = policy_from_options(opts)
    assert policy.log_growth_seconds == _CUSTOM_LOG_GROWTH


def test_log_growth_uses_default_when_not_set() -> None:
    """When log_growth_seconds is not set, TimeoutPolicy uses LOG_GROWTH_SECONDS (30.0)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
    )
    policy = policy_from_options(opts)
    assert policy.log_growth_seconds == _LOG_GROWTH


def test_log_growth_none_is_preserved() -> None:
    """log_growth_seconds=None is preserved as None (operator opt-out)."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        log_growth_seconds=None,
    )
    policy = policy_from_options(opts)
    assert policy.log_growth_seconds is None
