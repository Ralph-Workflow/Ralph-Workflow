import asyncio
import inspect
import json
import sys
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest

import ralph.process.manager as _mgr
from ralph.agents.executor import AgentExecutor
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.activity_router import ActivityRouter
from ralph.display.raw_overflow import close_all_raw_overflow_logs
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV
from ralph.pipeline.work_units import WorkUnit
from ralph.process import get_process_manager, reset_process_manager
from ralph.process.liveness import DefaultLivenessProbe
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakeControllableAsyncProcess

pytestmark = pytest.mark.subprocess_e2e

EXIT_CODE_FAILURE = 42
EXPECTED_ACTIVITY_ENTRIES = 2


def ignore_output(_line: str) -> None:
    return None


def ignore_status(_status: str) -> None:
    return None


def make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Unit {unit_id}")


@pytest.fixture(autouse=True)
def _reset_pm() -> Iterator[None]:
    reset_process_manager()
    yield
    get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


@pytest.mark.asyncio
async def test_protocol_isinstance() -> None:
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('ok')"])
    assert isinstance(executor, AgentExecutor)
    assert "command" not in inspect.signature(SubprocessAgentExecutor.run).parameters


@pytest.mark.asyncio
async def test_streams_output() -> None:
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", "print('line1'); print('line2'); print('line3')"]
    )
    unit = make_unit("test-A")

    collected: list[str] = []
    result = await executor.run(
        unit,
        on_output=collected.append,
        on_status=ignore_status,
    )

    assert result.unit_id == "test-A"
    assert result.exit_code == 0
    assert any("line1" in s for s in collected)
    assert any("line2" in s for s in collected)
    assert any("line3" in s for s in collected)


@pytest.mark.asyncio
async def test_exit_code_propagates() -> None:
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", f"import sys; sys.exit({EXIT_CODE_FAILURE})"]
    )
    unit = make_unit("test-B")
    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )
    assert result.exit_code == EXIT_CODE_FAILURE


@pytest.mark.asyncio
@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
async def test_cancel_kills_process_group() -> None:
    unit = make_unit("test-C")
    started = asyncio.Event()

    executor = SubprocessAgentExecutor(
        [
            sys.executable,
            "-c",
            ("import sys, signal; print('ready'); sys.stdout.flush(); signal.pause()"),
        ]
    )

    def on_output(line: str) -> None:
        if "ready" in line:
            started.set()

    task = asyncio.create_task(executor.run(unit, on_output=on_output, on_status=ignore_status))

    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_duration_ms_populated() -> None:
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('done')"])
    unit = make_unit("test-D")
    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_final_message_from_last_line() -> None:
    executor = SubprocessAgentExecutor([sys.executable, "-c", "print('first'); print('last')"])
    unit = make_unit("test-E")
    last: list[str] = []
    result = await executor.run(
        unit,
        on_output=last.append,
        on_status=ignore_status,
    )
    assert result.final_message == (last[-1] if last else "")


@pytest.mark.asyncio
async def test_subprocess_executor_registers_scoped_agent_label_for_liveness() -> None:
    output_ready = asyncio.Event()
    completion = asyncio.Event()

    proc = FakeControllableAsyncProcess(
        pid=42,
        stdout_data=b"ready\n",
        completion_event=completion,
    )

    async def fake_factory(
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        return proc

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=fake_factory,
    )
    original_singleton = _mgr._pm_state.instance
    _mgr._pm_state.instance = pm

    executor = SubprocessAgentExecutor(
        ["fake-cmd"],
        extra_env={str(AGENT_LABEL_SCOPE_ENV): "run-scope-456"},
        _pm=pm,
    )
    unit = make_unit("worker-a")

    def on_output(line: str) -> None:
        if "ready" in line:
            output_ready.set()

    finished = asyncio.Event()

    async def _run() -> None:
        await executor.run(unit, on_output=on_output, on_status=ignore_status)
        finished.set()

    try:
        task = asyncio.create_task(_run())
        await asyncio.wait_for(output_ready.wait(), timeout=1.0)

        probe = DefaultLivenessProbe()
        assert probe.any_agent_active("agent:run-scope-456:") is True
        assert probe.any_agent_active("agent:other-scope:") is False

        completion.set()
        await asyncio.wait_for(task, timeout=1.0)
        assert finished.is_set() is True
    finally:
        _mgr._pm_state.instance = original_singleton


@pytest.mark.asyncio
async def test_activity_router_receives_valid_ndjson_and_non_json_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = ActivityRouter()
    python_in_path = next(
        p
        for p in (sys.executable, "/usr/bin/python3", "/usr/local/bin/python3")
        if p
        and not any(
            needle in p for needle in ("agy", "claude", "opencode", "codex", "aider", "gemini")
        )
    )
    executor = SubprocessAgentExecutor([python_in_path, "-c", "print('placeholder')"])
    executor.activity_router = router
    unit = make_unit("test-F")

    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.sanitize_display_line",
        lambda _raw: '{"content":"structured"}\nnot-json',
    )

    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )

    entries = router.get_buffer(unit.unit_id).snapshot()
    assert result.exit_code == 0
    assert len(entries) == EXPECTED_ACTIVITY_ENTRIES
    assert any("structured" in entry for entry in entries)
    assert any("not-json" in entry for entry in entries)


@pytest.mark.asyncio
async def test_activity_router_raw_log_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    router = ActivityRouter()
    max_bytes = 1024
    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.DEFAULT_MAX_OVERFLOW_FILE_BYTES",
        max_bytes,
    )
    payload = "x" * 200
    command = [
        sys.executable,
        "-c",
        "for _ in range(20): print('" + payload + "')",
    ]
    executor = SubprocessAgentExecutor(
        command,
        activity_router=router,
        raw_overflow_root=tmp_path,
    )
    unit = make_unit("bounded-raw-log")

    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )

    assert result.exit_code == 0
    assert router.get_buffer(unit.unit_id).snapshot()

    log_path = tmp_path / ".agent" / "raw" / "bounded-raw-log.log"
    assert log_path.exists()
    assert log_path.stat().st_size <= max_bytes

    previous_size = log_path.stat().st_size
    raw_log = executor._get_raw_log(unit.unit_id)
    assert raw_log.append(payload) is False
    assert log_path.stat().st_size == previous_size


@pytest.mark.asyncio
async def test_drop_unit_releases_raw_log_entry(tmp_path: object) -> None:
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", "print('drop-unit-output')"],
        activity_router=ActivityRouter(),
        raw_overflow_root=tmp_path,
    )
    unit = make_unit("drop-unit-target")

    result = await executor.run(
        unit,
        on_output=ignore_output,
        on_status=ignore_status,
    )

    assert result.exit_code == 0

    assert unit.unit_id in executor._raw_logs, (
        f"run() should populate _raw_logs[{unit.unit_id!r}],"
        f" got keys={list(executor._raw_logs.keys())}"
    )

    executor.drop_unit(unit.unit_id)
    assert unit.unit_id not in executor._raw_logs, (
        f"drop_unit should remove _raw_logs[{unit.unit_id!r}],"
        f" got keys={list(executor._raw_logs.keys())}"
    )

    executor.drop_unit(unit.unit_id)
    assert unit.unit_id not in executor._raw_logs

    executor.drop_unit("never-existed-unit")


@pytest.mark.asyncio
async def test_drop_unit_does_not_affect_other_units(tmp_path: object) -> None:
    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", "print('multi-unit')"],
        activity_router=ActivityRouter(),
        raw_overflow_root=tmp_path,
    )

    units = [make_unit(f"unit-{idx}") for idx in range(3)]
    for unit in units:
        await executor.run(
            unit,
            on_output=ignore_output,
            on_status=ignore_status,
        )

    for unit in units:
        assert unit.unit_id in executor._raw_logs

    executor.drop_unit(units[1].unit_id)

    assert units[0].unit_id in executor._raw_logs
    assert units[1].unit_id not in executor._raw_logs
    assert units[2].unit_id in executor._raw_logs


@pytest.mark.asyncio
async def test_verbatim_capture_receives_the_undecorated_wire_frame(
    tmp_path: Path,
) -> None:
    frame = json.dumps({"type": "item.completed", "text": "y" * 400})
    assert len(frame) > 200, "the frame must exceed the display truncation point"

    executor = SubprocessAgentExecutor(
        [sys.executable, "-c", f"print({frame!r})"],
        activity_router=ActivityRouter(),
        raw_overflow_root=tmp_path,
    )

    await executor.run(
        make_unit("verbatim-unit"),
        on_output=ignore_output,
        on_status=ignore_status,
    )
    close_all_raw_overflow_logs()

    captured = (tmp_path / ".agent" / "raw" / "verbatim-unit.log").read_text(encoding="utf-8")

    assert frame in captured, "the capture must hold the whole frame"
    assert "…" not in captured, "the capture must not hold display-truncated text"
    assert json.loads(captured.strip()) == json.loads(frame)
