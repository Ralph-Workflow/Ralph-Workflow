from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.git.proposal_seal import Seal

pytestmark = pytest.mark.timeout_seconds(10)


class _ExplodingSeal:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __call__(self, *_args: object, **_kwargs: object) -> None:
        raise self._exc


def test_baseline_seal_regression_failure_is_a_terminal_gate_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        run_loop,
        "seal_baseline",
        _ExplodingSeal(RuntimeError("seal exploded")),
    )
    policy_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"

    result = run_loop.resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=load_policy(policy_dir),
        initial_state=None,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=tmp_path,
    )

    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert result.rollback_baseline_ref is None
    assert "gate failed" in (result.last_error or "").lower()
    assert "baseline" in (result.last_error or "").lower()


class _FakeSeal:
    def __init__(self, ref: str) -> None:
        self._ref = ref

    def __call__(self, *_args: object, **_kwargs: object) -> Seal:
        from ralph.git.proposal_seal import Seal

        return Seal(ref=self._ref, commit_sha="1" * 40, tree_sha="0" * 40)


def test_successful_baseline_seal_records_the_seal_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_loop,
        "seal_baseline",
        _FakeSeal("refs/ralph/baselines/fake-run"),
    )
    state = PipelineState(phase="explore", rollback_baseline_ref=None)

    result = run_loop._capture_rollback_baseline(state, Path(f"/tmp/{uuid.uuid4().hex}"))

    assert result.rollback_baseline_ref == "refs/ralph/baselines/fake-run"
