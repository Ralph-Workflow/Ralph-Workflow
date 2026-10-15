"""Static-scan and behavior tests: PipelineState.set_waiting owns is_waiting_state.

Contract: ``is_waiting_state`` may only be written inside
``ralph/pipeline/state.py`` (via ``set_waiting`` / ``clear_waiting``). All
other modules must route transitions through that seam; this test scans the
source tree for direct ``is_waiting_state=`` writer sites.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.pipeline.state import PipelineState, WaitReason

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STATE_MODULE = _REPO_ROOT / "ralph" / "pipeline" / "state.py"
_EXEMPT_MODULES: set[Path] = set()

# Writer-shape: keyword assignment of the flag inside a call
# (``copy_with(..., is_waiting_state=...)`` or ``model_copy(update={...})``
# after normalization). Docstring/comment mentions are not writers.


def _flag_writer_lines(source: str) -> list[int]:
    """Line numbers of ``copy_with``/``model_copy`` calls writing the flag."""
    lines: list[int] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("copy_with", "model_copy"):
            continue
        for kw in node.keywords:
            if kw.arg == "is_waiting_state":
                lines.append(kw.value.lineno)
    return lines


def _make_state() -> PipelineState:
    policy = MagicMock()
    policy.entry_phase = "development"
    return PipelineState.from_policy(policy)


@pytest.mark.timeout_seconds(10)
def test_no_direct_writers_outside_state_module() -> None:
    offenders: list[str] = []
    for path in (_REPO_ROOT / "ralph").rglob("*.py"):
        if path == _STATE_MODULE or path in _EXEMPT_MODULES:
            continue
        for lineno in _flag_writer_lines(path.read_text(encoding="utf-8")):
            offenders.append(
                f"{path.relative_to(_REPO_ROOT)}:{lineno}: direct is_waiting_state writer"
            )
    assert offenders == []


@pytest.mark.parametrize("reason", list(WaitReason))
def test_set_waiting_raises_flag_and_records_reason(reason: WaitReason) -> None:
    state = _make_state()
    waiting = state.set_waiting(reason, until_monotonic_ms=12345)
    assert waiting is not state
    assert waiting.is_waiting_state is True
    assert waiting.wait_reason is reason
    assert waiting.wait_until_monotonic_ms == 12345
    # Original snapshot untouched (immutability).
    assert state.is_waiting_state is False


def test_set_waiting_allows_none_deadline() -> None:
    waiting = _make_state().set_waiting(WaitReason.COOLDOWN, until_monotonic_ms=None)
    assert waiting.is_waiting_state is True
    assert waiting.wait_reason is WaitReason.COOLDOWN
    assert waiting.wait_until_monotonic_ms is None


def test_clear_waiting_lowers_flag_and_resets_metadata() -> None:
    state = _make_state().set_waiting(WaitReason.EXHAUSTED_RETRY, until_monotonic_ms=99)
    cleared = state.clear_waiting()
    assert cleared.is_waiting_state is False
    assert cleared.wait_reason is None
    assert cleared.wait_until_monotonic_ms is None


def test_wait_reason_is_closed_string_enum() -> None:
    assert {member.value for member in WaitReason} == {
        "all_agents_unavailable",
        "exhausted_retry",
        "cooldown",
    }
    assert WaitReason.COOLDOWN == "cooldown"
