from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
    return RebaseState(
        last_action="conflict",
        unresolved_integration_carried=True,
        resolution_exhausted=True,
        resolution_exhaustion_reason="all strategies failed",
    )


def test_correction_clears_every_unresolved_latch() -> None:
    corrected = correct_overtaken_persisted_record(_unresolved_state())

    assert corrected.integration_unresolved is False
    assert corrected.resolution_exhausted is False
    assert corrected.resolution_exhaustion_reason is None
    assert corrected.unresolved_integration_carried is False
    assert corrected.recovery_record_retained is False
    assert corrected.last_outcome is not None and corrected.last_outcome.value == "completed"
    assert corrected.last_reason is not None and "written back" in corrected.last_reason


def test_clean_live_tree_with_unresolved_record_writes_correction_back() -> None:
    received: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        Path("/tmp"),
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


def test_gate_pass_persists_correction_through_the_checkpoint_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline import run_loop
    from ralph.pipeline.checkpoint import Checkpoint
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy
    from ralph.workspace.scope import WorkspaceScope

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
    from ralph.pipeline.integration_resolution import inspect_integration_resolution as real_inspect

    def _clean_probe_inspect(
        root: Path,
        state: RebaseState,
        *,
        porcelain: Callable[[Path], tuple[bool, str]] | None = None,
        rebase_active: Callable[[Path], bool] | None = None,
        merge_status: Callable[[Path], str] | None = None,
        write_back: Callable[[RebaseState], None] | None = None,
    ) -> object:
        return real_inspect(
            root,
            state,
            porcelain=porcelain if porcelain is not None else _porcelain_clean,
            rebase_active=rebase_active if rebase_active is not None else _rebase_clean,
            merge_status=merge_status if merge_status is not None else _merge_clean,
            write_back=write_back,
        )

    monkeypatch.setattr(run_loop, "inspect_integration_resolution", _clean_probe_inspect)

    blocked = run_loop._block_unresolved_integration(persisted, context, "planning")

    assert blocked is None
    reloaded = checkpoint.load()
    assert reloaded is not None
    assert reloaded.rebase.integration_unresolved is False
    assert reloaded.rebase.resolution_exhausted is False
    assert reloaded.rebase.last_reason is not None
    assert "written back" in reloaded.rebase.last_reason


class _Registry:
    def get(self, name: str) -> None:
        del name
