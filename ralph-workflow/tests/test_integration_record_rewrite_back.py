"""AC-11(f): overriding the persisted record writes the correction back.

When the live tree provably contradicts the durable integration record --
a clean, fully readable readiness assessment against a record that still
says unresolved -- the correction must reach the durable record, not
merely the operator log. Otherwise the record and the tree disagree again
on every later pass and the same override is rediscovered forever.

Fail-closed cases are asserted alongside: an unreadable probe and a dirty
tree both leave the persisted evidence exactly as it was.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline.integration_resolution import (
    UnknownKind,
    correct_overtaken_persisted_record,
    inspect_integration_resolution,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch


def _porcelain_clean(root: Path) -> tuple[bool, str]:
    return True, ""


def _rebase_clean(root: Path) -> bool:
    return False


def _merge_clean(root: Path) -> str:
    return MERGE_STATE_NONE


def _unresolved_state() -> RebaseState:
    """A durable record latched as unresolved by an earlier conflict seam."""
    return RebaseState(
        last_action="conflict",
        unresolved_integration_carried=True,
        resolution_exhausted=True,
        resolution_exhaustion_reason="all strategies failed",
    )


def test_correction_clears_every_unresolved_latch() -> None:
    """The derived correction reports no unresolved ownership at all."""
    corrected = correct_overtaken_persisted_record(_unresolved_state())

    assert corrected.integration_unresolved is False
    assert corrected.resolution_exhausted is False
    assert corrected.resolution_exhaustion_reason is None
    assert corrected.unresolved_integration_carried is False
    assert corrected.recovery_record_retained is False
    assert corrected.last_outcome is not None and corrected.last_outcome.value == "completed"
    assert corrected.last_reason is not None and "written back" in corrected.last_reason


def test_clean_live_tree_with_unresolved_record_writes_correction_back() -> None:
    """A clean live tree against a latched record must produce the correction.

    The write-back sink stands in for the durable checkpoint / record
    store: whatever it receives is what a restart would read, so
    asserting on it is the restart-safe form of this proof.
    """
    received: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        Path("/tmp"),  # probes are faked; the root is never touched
        _unresolved_state(),
        write_back=received.append,
        porcelain=_porcelain_clean,
        rebase_active=_rebase_clean,
        merge_status=_merge_clean,
    )

    assert verdict.dispatch_allowed
    assert len(received) == 1
    corrected = received[0]
    assert corrected.integration_unresolved is False
    assert corrected.resolution_exhausted is False


def test_unknown_probe_fails_closed_and_writes_nothing() -> None:
    """An unreadable probe keeps the persisted evidence blocking untouched."""
    received: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        Path("/tmp"),
        _unresolved_state(),
        write_back=received.append,
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.unknown_kinds == (UnknownKind.PORCELAIN_UNREADABLE,)
    assert not verdict.dispatch_allowed
    assert received == []


def test_dirty_tree_with_unresolved_record_writes_nothing() -> None:
    """Uncommitted tracked work never proves the persisted record overtaken."""
    received: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        Path("/tmp"),
        _unresolved_state(),
        write_back=received.append,
        porcelain=lambda _: (True, " M tracked.py"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert not verdict.integration_ready
    assert received == []


def _rebase_probe_down(root: Path) -> bool:
    raise RuntimeError("probe down")


def test_unknown_kinds_with_clean_porcelain_fail_closed_via_rebase_probe() -> None:
    """A rebase probe that cannot answer defers; no correction is minted."""
    received: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        Path("/tmp"),
        _unresolved_state(),
        write_back=received.append,
        porcelain=_porcelain_clean,
        rebase_active=_rebase_probe_down,
        merge_status=_merge_clean,
    )

    assert verdict.unknown_kinds == (UnknownKind.REBASE_STATE_UNREADABLE,)
    assert received == []


@pytest.mark.parametrize(
    ("state", "changed"),
    [
        (_unresolved_state(), True),
        (RebaseState(), False),
    ],
)
def test_correction_only_when_state_carries_evidence(state: RebaseState, changed: bool) -> None:
    """An already-clean state is returned unchanged; no spurious write-back."""
    received: list[RebaseState] = []
    inspect_integration_resolution(
        Path("/tmp"),
        state,
        write_back=received.append,
        porcelain=_porcelain_clean,
        rebase_active=_rebase_clean,
        merge_status=_merge_clean,
    )

    assert (len(received) == 1) is changed


# ---------------------------------------------------------------------------
# Production wiring: the dispatch gate persists the correction (AC-11(f)).
# ---------------------------------------------------------------------------


def test_gate_pass_persists_correction_through_the_checkpoint_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """The run-loop dispatch gate rewrites the durable record, restart-safe.

    Drives the real production path -- ``run_loop._block_unresolved_integration``
    with its default checkpoint hook -- against a latched unresolved state and
    a faked clean live tree. The durable proof is the checkpoint FILE the next
    run would load: it must no longer report unresolved, and the weaker
    plain containment release must not have been persisted instead (the
    typed correction carries the completed outcome; the release does not).
    """
    from unittest.mock import MagicMock

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline import run_loop
    from ralph.pipeline.checkpoint import Checkpoint
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy
    from ralph.workspace.scope import WorkspaceScope

    # No durable integration record on disk: the latching lives in the
    # RebaseState the checkpoint carried, which is exactly what a clean
    # live tree overtakes.
    persisted = PipelineState(phase="planning", rebase=_unresolved_state())
    checkpoint = Checkpoint(tmp_path / "checkpoint.json")
    checkpoint.save(persisted)

    context = run_loop._LoopContext(
        policy_bundle=load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults"),
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry=_Registry(),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=MagicMock(),
        is_quiet=True,
        latest_state=[persisted],
    )
    # The gate's probes are faked to a fully readable, integration-ready
    # tree; every other collaborator keeps its production implementation.
    # The wrapper forwards the gate's own ``write_back`` kwarg so the real
    # observation layer (and its AC-11(f) correction) stays in the loop.
    from ralph.pipeline.integration_resolution import inspect_integration_resolution as real_inspect

    def _clean_probe_inspect(root: Path, state: RebaseState, **kwargs: object) -> object:
        write_back = kwargs.pop("write_back", None)
        kwargs.clear()
        return real_inspect(
            root,
            state,
            porcelain=_porcelain_clean,
            rebase_active=_rebase_clean,
            merge_status=_merge_clean,
            write_back=cast("Callable[[RebaseState], None] | None", write_back),
        )

    monkeypatch.setattr(run_loop, "inspect_integration_resolution", _clean_probe_inspect)

    blocked = run_loop._block_unresolved_integration(persisted, context, "planning")

    # The gate allowed ordinary dispatch, and what a restart reads from
    # the canonical checkpoint path no longer reports unresolved.
    assert blocked is None
    reloaded = checkpoint.load()
    assert reloaded is not None
    assert reloaded.rebase.integration_unresolved is False
    assert reloaded.rebase.resolution_exhausted is False
    # The typed correction (not the weaker plain release) is what got
    # persisted: it records the completed outcome with the write-back reason.
    assert reloaded.rebase.last_reason is not None
    assert "written back" in reloaded.rebase.last_reason


class _Registry:
    """Minimal agent registry the gate path never consults."""

    def get(self, name: str) -> None:
        """No agents are installed; the gate path must not need one."""
        del name
