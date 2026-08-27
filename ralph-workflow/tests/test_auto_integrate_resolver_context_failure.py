"""The rebase-stop resolver declines an unusable target context; it never raises.

``build_agent_rebase_stop_resolver`` documents that the callable it
returns never raises, and the whole rebase-resolution loop is built on
that: :func:`ralph.pipeline.conflict_resolution.rebase_loop._resolve_stops`
treats the return value as the decision and has only a blanket
``except`` above it, which turns any escape into an unexplained
"resolution loop failed" line.

Entering ``workspace_context`` re-reads the TARGET worktree's config,
policy and agent registry from disk on every conflict, so it is the one
step inside the resolver that a mid-run edit can break. An operator
saving ``.agent/ralph-workflow.toml`` with a missing comma used to be
enough: from that moment the run raised out of the resolver on every
conflict, silently lost agent conflict resolution for the rest of its
life, and named the parse error nowhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import auto_integrate_agent as agent_module
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, RebaseStopResolver

if TYPE_CHECKING:
    import pytest


class _UnreadableConfigError(RuntimeError):
    """Stand-in for the config loader's parse failure."""


def _build_resolver(display: MagicMock) -> RebaseStopResolver:
    return agent_module.build_agent_rebase_stop_resolver(
        policy_bundle=MagicMock(name="policy_bundle"),
        registry=MagicMock(name="registry"),
        display=display,
        config=MagicMock(name="config"),
        pipeline_deps=MagicMock(name="pipeline_deps"),
        workspace_scope=MagicMock(name="workspace_scope"),
    )


def _stop() -> RebaseStop:
    return RebaseStop(
        sha="deadbeef",
        subject="replay the feature commit",
        conflicted_files=("src/omega.py",),
        stop_index=1,
        stop_cap=5,
    )


def test_unreadable_target_config_declines_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(_root: Path) -> object:
        raise _UnreadableConfigError("Unclosed array (at line 75, column 1)")

    monkeypatch.setattr(agent_module, "workspace_context", _raise)
    resolver = _build_resolver(MagicMock(name="display"))

    assert resolver(tmp_path, "main", _stop()) is False


def test_unreadable_target_config_is_reported_to_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The decline must name the real cause, not just fail quietly.

    A resolver that declines without saying why is indistinguishable
    from one that had nothing to do, which is how a broken config file
    stayed invisible for a whole run.
    """
    emitted: list[str] = []

    def _raise(_root: Path) -> object:
        raise _UnreadableConfigError("Unclosed array (at line 75, column 1)")

    monkeypatch.setattr(agent_module, "workspace_context", _raise)
    monkeypatch.setattr(
        agent_module,
        "emit_integration_warn_line",
        lambda _display, message: emitted.append(message),
    )
    resolver = _build_resolver(MagicMock(name="display"))

    assert resolver(tmp_path, "main", _stop()) is False
    assert len(emitted) == 1
    assert "Unclosed array (at line 75, column 1)" in emitted[0]
