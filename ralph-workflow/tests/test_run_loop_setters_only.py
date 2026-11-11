from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = [
    _REPO_ROOT / "ralph" / "pipeline" / "run_loop.py",
    _REPO_ROOT / "ralph" / "pipeline" / "runner.py",
]


def _offender_lines(path: Path, flag: str) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return offenders
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"copy_with", "model_copy"}:
            continue
        window = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        if f"{flag}=" in window:
            offenders.append((node.lineno, lines[node.lineno - 1].strip()))
    return offenders


def test_no_direct_is_waiting_state_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "is_waiting_state")
    ]
    assert offenders == []


def test_no_direct_recovery_record_retained_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "recovery_record_retained")
    ]
    assert offenders == []


def test_no_direct_operation_display_state_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "operation_display_state")
    ]
    assert offenders == []


def test_no_direct_last_fan_out_decision_writers() -> None:
    offenders = [
        f"{path.name}:{lineno}: {text}"
        for path in _TARGETS
        for lineno, text in _offender_lines(path, "last_fan_out_decision")
    ]
    assert offenders == []


def test_setter_usage_present_in_run_loop() -> None:
    run_loop = _TARGETS[0].read_text(encoding="utf-8")
    assert "set_waiting(WaitReason." in run_loop
    assert ".clear_waiting()" in run_loop
    assert ".set_recovery_record_retained(" in run_loop
    assert ".set_operation_display_state(" in run_loop
    fan_out_authority = _TARGETS[0].parent.joinpath("_fan_out_authority.py").read_text()
    assert ".set_fan_out_decision(" in fan_out_authority


def test_apply_step_integration_outcome_surfaces_waiting_display_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import load_policy

    policy_bundle = load_policy(
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    )
    monkeypatch.setattr(runner_module, "read_record", lambda _root: None)
    rebase = RebaseState(recovery_record_retained=True)
    state = PipelineState(phase="development", rebase=rebase)
    monkeypatch.setattr(
        runner_module,
        "_reconcile_rebase_if_live_resolved",
        lambda _scope, rebase: rebase,
    )
    scope = SimpleNamespace(root=tmp_path)
    out = runner_module._apply_step_integration_outcome(state, scope, policy_bundle, rebase)
    assert out.rebase.operation_display_state == "waiting"
    assert out.rebase.operation_display_state != "intervention_required"
