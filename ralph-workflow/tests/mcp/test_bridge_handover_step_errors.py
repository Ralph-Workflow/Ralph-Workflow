"""Tests that make-before-break McpServerErrors name the failing handover step (DEC-002).

Every McpServerError raised from the make-before-break path must carry
``handover_step`` naming the stage that failed (spawn, verify, pause-ack, resume,
promote, drain), and the drain stage's step must not be overwritten by promote.
"""

import os
import pathlib
from unittest.mock import patch

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    compute_tool_set_hash,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from tests.mcp._lifecycle_tool_hash_seams import (
    TOOLS_BASE,
    _bridge_with_old_child,
    _captured_logs,
    _handover_candidate,
    _make_inner,
    _SyntheticResponder,
    register_synthetic_responders,
)


@pytest.fixture(scope="function")
def synthetic_responders(request: pytest.FixtureRequest) -> list[_SyntheticResponder]:
    """Own teardown of every synthetic responder (see the shared seams module)."""
    return register_synthetic_responders(request)


# ---------------------------------------------------------------------------
# DEC-002: make-before-break errors name the failing handover stage
# ---------------------------------------------------------------------------


def test_spawn_stage_failure_is_stamped_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A candidate-spawn McpServerError names the spawn stage (field and log)."""
    raised: list[McpServerError] = []

    def _explode() -> StandaloneMcpProcess:
        err = McpServerError("spawn refused", restart_count=1)
        raised.append(err)
        raise err

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, restart_fn=_explode)
    with _captured_logs() as records:
        assert bridge._run_with_make_before_break() is False
    # Non-terminal under the C2 contract: the old child keeps serving...
    assert bridge.process.pid == 1001
    # ...but the error itself still names the stage that produced it.
    assert len(raised) == 1
    assert raised[0].handover_step == "spawn_candidate"
    assert any("handover_step=spawn_candidate" in record for record in records)


def test_verify_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A verification-stage McpServerError escapes the make-before-break path stamped."""
    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)

    def _verify(_candidate: StandaloneMcpProcess) -> bool:
        raise McpServerError("verification exploded", restart_count=1)

    with (
        patch.object(bridge, "_verify_candidate", _verify),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "verify_candidate"


def test_pause_ack_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A pause/ack failure names the pause-ack stage, not a generic handover step."""

    def _explode(_pause_path: pathlib.Path) -> None:
        raise McpServerError("pause file write failed", restart_count=1)

    old_inner = _make_inner(poll_result=None, pid=os.getpid(), session_file=tmp_path / "o.json")
    candidate = _make_inner(poll_result=None, pid=2002, session_file=tmp_path / "c.json")
    candidate.stage_id = "cand-pause"
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-pause",
        workspace_root=tmp_path,
        sleep_fn=lambda _seconds: None,
    )

    with (
        patch.object(lifecycle, "request_old_child_pause", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._hand_over_accept(old_inner, candidate)
    assert excinfo.value.handover_step == "pause_ack"


def test_resume_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A resume-sentinel failure names the resume stage."""

    def _explode(_resume_path: pathlib.Path) -> None:
        raise McpServerError("resume file write failed", restart_count=1)

    old_inner = _make_inner(poll_result=None, pid=os.getpid(), session_file=tmp_path / "o.json")
    candidate = _make_inner(poll_result=None, pid=2002, session_file=tmp_path / "c.json")
    candidate.stage_id = "cand-resume"
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-resume",
        workspace_root=tmp_path,
        sleep_fn=lambda _seconds: None,
    )

    with (
        patch.object(lifecycle, "wait_for_old_child_pause_ack", lambda *a, **k: True),
        patch.object(lifecycle, "signal_candidate_resume", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._hand_over_accept(old_inner, candidate)
    assert excinfo.value.handover_step == "resume_candidate"


def test_promote_stage_failure_escapes_with_handover_step(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    """A promotion failure names the promote stage."""

    def _explode(_candidate: StandaloneMcpProcess, _old: StandaloneMcpProcess) -> None:
        raise McpServerError("promotion failed", restart_count=1)

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)
    _handover_candidate(
        tmp_path,
        tools=TOOLS_BASE,
        pid=2002,
        stage_id="cand-promote",
        label="promote",
        responders=synthetic_responders,
    )
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = list(TOOLS_BASE)

    with (
        patch.object(bridge, "_verify_candidate", lambda _c: True),
        patch.object(bridge, "_promote_and_reap", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "promote_candidate"


def test_drain_stage_failure_is_not_overwritten_by_the_promote_stage(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    """The innermost stage wins: a drain failure keeps its own step."""

    def _explode(_old: StandaloneMcpProcess) -> None:
        raise McpServerError("drain failed", restart_count=1)

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)
    _handover_candidate(
        tmp_path,
        tools=TOOLS_BASE,
        pid=2002,
        stage_id="cand-drain",
        label="drain",
        responders=synthetic_responders,
    )
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = list(TOOLS_BASE)

    with (
        patch.object(bridge, "_verify_candidate", lambda _c: True),
        patch.object(bridge, "_drain_old", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "drain_old_child"
