"""Fast black-box proof that conflicted rebases use the injected resolver.

The former module paid for separate real repositories for success, decline,
missing resolvers, marker rejection, unrequested-path rejection, and two replay
stops.  Those observable behaviours remain covered by
``test_conflict_resolution_rebase_loop.py`` and the representative real-Git
conflict tests in ``test_auto_integrate.py``.  This file retains its unique
composition contract through the existing rebase engine seams: a conflict is
offered to the injected resolver before endpoint-merge fallback, and a resolved
stop is reported as a successful linear rebase.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase import RebaseConflicts, RebaseSuccess
from ralph.pipeline import auto_integrate_rebase_merge as merge_engine

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_rebase_merge import RebaseRunResult

pytestmark = pytest.mark.subprocess_e2e


def test_conflicted_rebase_uses_injected_resolver_before_merge_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolved stop is observable as rebase success with no merge."""
    resolver_calls: list[object] = []
    fallback_calls: list[Path] = []

    def _resolver(_root: Path, _target: str, stop: object) -> bool:
        resolver_calls.append(stop)
        return True

    def _routing_reason(_root: Path, _target: str) -> None:
        return None

    def _rebase_onto(
        _target: str, *, repo_root: Path
    ) -> RebaseConflicts:
        return RebaseConflicts(files=["shared.txt"])

    def _set_resolving(_root: Path, _value: bool) -> bool:
        return True

    def _resolve_in_progress(
        root: Path, target: str, received_resolver: object
    ) -> bool:
        assert root == Path("/workspace")
        assert target == "main"
        assert received_resolver is _resolver
        return True

    def _fallback(
        root: Path, _target: str, _outcome: object, _resolver: object
    ) -> RebaseRunResult:
        fallback_calls.append(root)
        raise AssertionError("resolved rebase must not fall back")

    monkeypatch.setattr(merge_engine, "_range_routing_reason", _routing_reason)
    monkeypatch.setattr(merge_engine, "rebase_onto", _rebase_onto)
    monkeypatch.setattr(merge_engine, "set_resolving_rebase", _set_resolving)
    monkeypatch.setattr(
        merge_engine, "resolve_rebase_in_progress", _resolve_in_progress
    )
    monkeypatch.setattr(merge_engine, "_fallback_to_endpoint_merge", _fallback)

    result = merge_engine.run_rebase_or_merge(
        Path("/workspace"),
        "main",
        conflict_resolver=None,
        rebase_stop_resolver=_resolver,
    )

    assert isinstance(result.rebase_outcome, RebaseSuccess)
    assert result.merge_attempted is False
    assert result.merge_outcome is None
    assert result.short_circuit is None
    assert fallback_calls == []
