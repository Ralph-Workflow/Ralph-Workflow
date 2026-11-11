from __future__ import annotations

import importlib
import io
import json
import re
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger

import ralph.pro_support as pkg
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.pro_support import (
    PROMPT_PATH as PKG_PROMPT_PATH,
)
from ralph.pro_support import (
    RALPH_WORKFLOW_PRO as PKG_PRO,
)
from ralph.pro_support import (
    RALPH_WORKSPACE as PKG_WORKSPACE,
)
from ralph.pro_support import (
    env as env_module,
)
from ralph.pro_support import (
    marker as marker_module,
)
from ralph.pro_support.heartbeat import ProHeartbeatClient
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.marker import read_marker_file
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.pro_support.workspace import resolve_pro_workspace
from ralph.recovery.controller import RecoveryController

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(5)


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(
        self,
        status_code: int = 200,
        tick_signal: threading.Event | threading.Semaphore | None = None,
    ) -> None:
        self.posts: list[dict[str, object]] = []
        self._status_code = status_code
        self._tick_signal = tick_signal

    def post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self._tick_signal is not None:
            if hasattr(self._tick_signal, "release"):
                self._tick_signal.release()
            else:
                self._tick_signal.set()
        return _FakeResponse(self._status_code)

    def close(self) -> None:
        pass


class _Clock:
    def __init__(self) -> None:
        self._now = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, delta: float) -> None:
        with self._lock:
            self._now += delta


def _make_heartbeat_client(
    fake: _FakeClient,
    clock: _Clock,
) -> ProHeartbeatClient:
    return ProHeartbeatClient(
        run_id="run-contract",
        token="token-contract",
        base_url="http://localhost:7432",
        pid=4242,
        interval_seconds=0.001,
        timeout_seconds=0.5,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )


def test_section_3_env_var_set_is_exactly_three() -> None:
    constants: tuple[str, ...] = (
        env_module.RALPH_WORKFLOW_PRO,
        env_module.RALPH_WORKSPACE,
        env_module.PROMPT_PATH,
    )
    assert constants == ("RALPH_WORKFLOW_PRO", "RALPH_WORKSPACE", "PROMPT_PATH")


def test_section_3_package_re_exports_match_module_attrs() -> None:
    assert (PKG_PRO, PKG_WORKSPACE, PKG_PROMPT_PATH) == (
        env_module.RALPH_WORKFLOW_PRO,
        env_module.RALPH_WORKSPACE,
        env_module.PROMPT_PATH,
    )


def test_section_3_helpers_do_not_read_a_foreign_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_NOT_A_CONTRACT_VAR", "1")
    monkeypatch.delenv(env_module.RALPH_WORKFLOW_PRO, raising=False)
    monkeypatch.delenv(env_module.RALPH_WORKSPACE, raising=False)
    monkeypatch.delenv(env_module.PROMPT_PATH, raising=False)
    assert env_module.is_pro_mode() is False
    assert env_module.get_ralph_workspace() is None
    assert env_module.get_prompt_path() is None


def test_section_3_ralph_workspace_is_honoured(tmp_path: Path) -> None:
    expected = (tmp_path / "ws").resolve()
    result = resolve_pro_workspace(env={"RALPH_WORKSPACE": str(expected)})
    assert result == expected


def test_section_3_prompt_path_absolute_is_honoured(tmp_path: Path) -> None:
    target = tmp_path / "operator.md"
    result = resolve_effective_prompt_path(tmp_path, env={"PROMPT_PATH": str(target)})
    assert result == target.resolve()


def test_section_3_prompt_path_relative_is_resolved_against_workspace(
    tmp_path: Path,
) -> None:
    result = resolve_effective_prompt_path(tmp_path, env={"PROMPT_PATH": "nested/operator.md"})
    assert result == (tmp_path / "nested" / "operator.md").resolve()


def test_section_3_prompt_path_unset_falls_back_to_workspace_prompt(
    tmp_path: Path,
) -> None:
    result = resolve_effective_prompt_path(tmp_path, env={})
    assert result == (tmp_path / "PROMPT.md").resolve()


def test_section_6_marker_reader_never_creates_marker(tmp_path: Path) -> None:
    marker_path = tmp_path / ".ralph" / "run.json"
    assert not marker_path.exists()
    assert read_marker_file(tmp_path) is None
    assert not marker_path.exists(), (
        "contract \u00a76: read_marker_file must not create the marker file"
    )


def test_section_6_marker_reader_does_not_mutate_existing_marker(
    tmp_path: Path,
) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    payload = {"runId": "abc", "port": 7777, "heartbeatToken": "tok"}
    marker_path = marker_dir / "run.json"
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    before_mtime_ns = marker_path.stat().st_mtime_ns
    before_text = marker_path.read_bytes()

    result = read_marker_file(tmp_path)

    after_mtime_ns = marker_path.stat().st_mtime_ns
    after_text = marker_path.read_bytes()
    assert result == payload
    assert before_mtime_ns == after_mtime_ns
    assert before_text == after_text


def test_section_6_pro_support_public_api_exposes_no_marker_writer() -> None:
    forbidden = frozenset(
        {
            "write_marker",
            "update_marker",
            "delete_marker",
            "create_marker",
            "write_marker_file",
            "patch_marker",
        }
    )
    pkg_public = frozenset(pkg.__all__)
    marker_public = frozenset(getattr(marker_module, "__all__", ()))

    leaked_pkg = pkg_public & forbidden
    leaked_marker = marker_public & forbidden
    assert not leaked_pkg, (
        f"contract \u00a76: pro_support.__all__ leaked marker writers: {sorted(leaked_pkg)}"
    )
    assert not leaked_marker, (
        f"contract \u00a76: marker module leaked writers: {sorted(leaked_marker)}"
    )


def test_section_6_heartbeat_client_does_not_create_or_modify_marker(
    tmp_path: Path,
) -> None:
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir()
    payload = {"runId": "fixed", "port": 7432, "heartbeatToken": "tok"}
    marker_path = marker_dir / "run.json"
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    before_mtime_ns = marker_path.stat().st_mtime_ns
    before_bytes = marker_path.read_bytes()

    tick_signal = threading.Semaphore(0)
    fake = _FakeClient(tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        assert tick_signal.acquire(timeout=4.0), "heartbeat worker failed to start"
        clock.advance(0.001)
        assert tick_signal.acquire(timeout=4.0), "heartbeat worker missed a tick"
    finally:
        client.stop()

    assert marker_path.exists()
    assert marker_path.stat().st_mtime_ns == before_mtime_ns
    assert marker_path.read_bytes() == before_bytes
    assert fake.posts, "expected at least one heartbeat tick"


def test_section_7_heartbeat_posts_required_fields() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        tick_signal.clear()
        clock.advance(0.001)
        assert tick_signal.wait(timeout=2.0)
    finally:
        client.stop()
    assert fake.posts, "no heartbeat posts captured"
    payload = fake.posts[0]["json"]
    assert isinstance(payload, dict)
    for required in ("run_id", "token", "status", "pid", "metadata"):
        assert required in payload, f"contract \u00a77: heartbeat payload missing {required!r}"
    assert payload["run_id"] == "run-contract"
    assert payload["token"] == "token-contract"
    assert payload["status"] == "running"
    assert payload["pid"] == 4242
    assert payload["metadata"] == {}
    assert fake.posts[0]["url"] == "http://localhost:7432/api/heartbeat"


def test_section_7_heartbeat_401_is_hard_stop() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(status_code=401, tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        assert tick_signal.wait(timeout=2.0), "expected the first 401 tick"
        posts_after_first = len(fake.posts)
        assert posts_after_first == 1
        tick_signal.clear()
        for _ in range(5):
            clock.advance(0.001)
        assert not tick_signal.wait(timeout=0.05), "client must not POST after 401"
        assert len(fake.posts) == 1
    finally:
        client.stop()


def test_section_7_heartbeat_404_is_hard_stop() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(status_code=404, tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        assert tick_signal.wait(timeout=2.0), "expected the first 404 tick"
        posts_after_first = len(fake.posts)
        assert posts_after_first == 1
        tick_signal.clear()
        for _ in range(5):
            clock.advance(0.001)
        assert not tick_signal.wait(timeout=0.05), "client must not POST after 404"
        assert len(fake.posts) == 1
    finally:
        client.stop()


def test_section_7_heartbeat_transient_error_continues() -> None:
    tick_signal = threading.Semaphore(0)
    fake = _FakeClient(status_code=503, tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        assert tick_signal.acquire(timeout=4.0), "heartbeat worker failed to start"
        clock.advance(0.001)
        assert tick_signal.acquire(timeout=4.0), "heartbeat worker missed a tick"
    finally:
        client.stop()
    assert len(fake.posts) >= 2, "transient errors must not stop the loop"


def test_section_7_heartbeat_request_carries_bounded_timeout() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        tick_signal.clear()
        clock.advance(0.001)
        assert tick_signal.wait(timeout=2.0)
    finally:
        client.stop()
    assert fake.posts
    for entry in fake.posts:
        assert entry["timeout"] is not None
        assert isinstance(entry["timeout"], (int, float))
        assert entry["timeout"] > 0


class _RecordingHeartbeat:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_running(self) -> bool:
        return self.started and not self.stopped


def _seed_pro_workspace(
    tmp_path: Path, *, run_id: str = "contract-run", token: str = "contract-tok"
) -> None:
    (tmp_path / "PROMPT.md").write_text("# contract\n", encoding="utf-8")
    marker_dir = tmp_path / ".ralph"
    marker_dir.mkdir(exist_ok=True)
    payload = {"runId": run_id, "port": 7432, "heartbeatToken": token}
    (marker_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")


def _make_pipeline_runner_with_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[[tuple[object, str, int | None]], tuple[int, _RecordingHeartbeat]]:
    run_loop_module = importlib.import_module("ralph.pipeline.run_loop")
    runner_module = importlib.import_module("ralph.pipeline.runner")
    recording = _RecordingHeartbeat()

    def _fake_start(_ws: object) -> _RecordingHeartbeat:
        recording.start()
        return recording

    monkeypatch.setattr(
        run_loop_module, "_start_pro_marker_watcher", lambda *_args, **_kwargs: (None, None)
    )
    monkeypatch.setattr(run_loop_module, "_start_pro_heartbeat_if_active", _fake_start)

    pipeline = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    agents = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=1),
            "development": AgentChainConfig(agents=["claude"], max_retries=1),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
        },
    )
    artifacts = ArtifactsPolicy(
        artifacts={
            "plan": ArtifactContract(
                drain="planning",
                artifact_type="plan",
            )
        }
    )
    bundle = PolicyBundle(pipeline=pipeline, agents=agents, artifacts=artifacts)

    config = MagicMock()
    config.general = MagicMock()
    config.general.auto_integrate_enabled = False
    config.general.verbosity = 0
    config.general.developer_iters = 1
    config.general.workflow = MagicMock()
    config.general.workflow.checkpoint_enabled = True
    config.general.max_same_agent_retries = 1
    config.general.checkpoint = MagicMock()
    config.general.parallel_max_workers = None

    state_in = PipelineState(phase="complete")

    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle)
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state_in)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )

    ctx = make_display_context()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kw: ctx)
    display = ParallelDisplay(workspace_root=tmp_path, display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_setup_connectivity_monitor",
        lambda _monitor: (MagicMock(), None),
    )

    def _run(
        inner_result: tuple[object, str, int | None],
    ) -> tuple[int, _RecordingHeartbeat]:
        nonlocal recording
        recording = _RecordingHeartbeat()
        monkeypatch.setattr(
            run_loop_module,
            "_run_inner_loop",
            lambda _state, _ctx, _prev: inner_result,
        )
        exit_code = run_loop_module.run(
            config,
            initial_state=state_in,
            display=display,
            display_context=ctx,
        )
        return exit_code, recording

    return _run


def test_section_4_pro_mode_clean_run_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_pro_workspace(tmp_path)
    state = PipelineState(phase="complete")
    run_pipeline = _make_pipeline_runner_with_heartbeat(monkeypatch, tmp_path)
    exit_code, recording = run_pipeline((state, "complete", None))
    assert exit_code == 0
    assert recording.started, "heartbeat should have been started in Pro mode"
    assert recording.stopped, "heartbeat should have been stopped during cleanup"


def test_section_4_pro_mode_failure_preserves_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_pro_workspace(tmp_path)
    state = PipelineState(phase="planning")
    run_pipeline = _make_pipeline_runner_with_heartbeat(monkeypatch, tmp_path)
    exit_code, recording = run_pipeline((state, "planning", 7))
    assert exit_code == 7, "contract \u00a74: non-zero step code must propagate"
    assert recording.stopped, "cleanup must still stop the heartbeat on failure"


def test_section_4_pro_mode_no_silent_zero_on_failure_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_pro_workspace(tmp_path)
    state = PipelineState(phase="planning")
    run_pipeline = _make_pipeline_runner_with_heartbeat(monkeypatch, tmp_path)
    for step_code in (1, 2, 7, 17, 42, 99, 130, 137, 143, 255):
        inner_result = (state, "planning", step_code)
        exit_code, _ = run_pipeline(inner_result)
        assert exit_code == step_code, (
            f"contract \u00a74: step code {step_code} must propagate; got {exit_code}"
        )


_JSON_OBJECT_LINE = re.compile(r"^\s*\{[^{}]*\}\s*$")
_JSON_ARRAY_LINE = re.compile(r"^\s*\[[^\[\]]*\]\s*$")


def _assert_utf8_newline_terminated(text: str) -> None:
    raw = text.encode("utf-8")
    round_tripped = raw.decode("utf-8")
    assert round_tripped == text
    assert text, "captured output is empty"
    assert text.endswith("\n"), (
        f"contract \u00a78: output must end with newline; tail={text[-32:]!r}"
    )
    assert "\r\n" not in text, "contract \u00a78: CRLF line endings are not allowed"


def _assert_no_bare_json_log_line(text: str) -> None:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _JSON_OBJECT_LINE.match(line) or _JSON_ARRAY_LINE.match(line):
            raise AssertionError(
                f"contract \u00a78: bare JSON log line at line {line_no}: {line!r}"
            )


def test_section_8_loguru_logger_emits_utf8_newline_terminated_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(0)

    sink_id = loguru_logger.add(
        sys.stderr,
        format="{message}",
        level="DEBUG",
    )
    try:
        loguru_logger.info("hello")
        loguru_logger.info("non-ascii: ünïcödé 漢字")
        loguru_logger.info("multi-line stub")
    finally:
        loguru_logger.remove(sink_id)

    assert caplog is not None
    _assert_utf8_newline_terminated("hello\nünïcödé 漢字\nmulti-line stub\n")


def test_section_8_loguru_does_not_emit_bare_json_log_line() -> None:
    captured: io.StringIO = io.StringIO()
    sink_id = loguru_logger.add(captured, format="{message}", level="DEBUG")
    try:
        loguru_logger.info("plain text")
        loguru_logger.info("with braces: the value is {not a json object}")
        loguru_logger.info("with brackets: a list of [items] is fine")
        loguru_logger.info("multiline = {\n  'json': 'object'\n}")
    finally:
        loguru_logger.remove(sink_id)

    text = captured.getvalue()
    _assert_utf8_newline_terminated(text)
    _assert_no_bare_json_log_line(text)


def test_section_8_heartbeat_client_does_not_log_payload_as_bare_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: io.StringIO = io.StringIO()
    sink_id = loguru_logger.add(captured, format="{message}", level="DEBUG")
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    clock = _Clock()
    client = _make_heartbeat_client(fake, clock)
    client.start()
    try:
        tick_signal.clear()
        clock.advance(0.001)
        assert tick_signal.wait(timeout=2.0)
    finally:
        client.stop()
        loguru_logger.remove(sink_id)

    text = captured.getvalue()
    _assert_no_bare_json_log_line(text)


def test_section_7_heartbeat_adopts_late_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_loop_module = importlib.import_module("ralph.pipeline.run_loop")
    runner_module = importlib.import_module("ralph.pipeline.runner")
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    _seed_pro_workspace(tmp_path, run_id="late", token="late-tok")

    state_in = PipelineState(phase="complete")
    bundle = PolicyBundle(
        pipeline=PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        ),
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"], max_retries=1),
                "development": AgentChainConfig(agents=["claude"], max_retries=1),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
            },
        ),
        artifacts=ArtifactsPolicy(
            artifacts={
                "plan": ArtifactContract(
                    drain="planning",
                    artifact_type="plan",
                )
            }
        ),
    )

    config = MagicMock()
    config.general = MagicMock()
    config.general.verbosity = 0
    config.general.developer_iters = 1
    config.general.workflow = MagicMock()
    config.general.workflow.checkpoint_enabled = True
    config.general.max_same_agent_retries = 1
    config.general.checkpoint = MagicMock()
    config.general.parallel_max_workers = None

    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: MagicMock(root=tmp_path, allowed_roots=[tmp_path]),
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _root: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _root: 0)
    monkeypatch.setattr(runner_module, "load_policy_bundle_for_run", lambda *_a, **_kw: bundle)
    monkeypatch.setattr(runner_module, "register_role_handlers", lambda _pp: None)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "create_initial_state", lambda *_a, **_kw: state_in)
    monkeypatch.setattr(
        run_loop_module,
        "_build_recovery_controller",
        lambda _state, _pp, _cfg: (
            MagicMock(
                spec=RecoveryController,
                event_bus=MagicMock(subscribe=lambda _cb: lambda: None),
            ),
            1,
        ),
    )
    ctx = make_display_context()
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kw: ctx)
    display = ParallelDisplay(workspace_root=tmp_path, display_context=ctx, is_quiet=True)
    monkeypatch.setattr(
        run_loop_module,
        "_setup_active_display",
        lambda *_a, **_kw: (display, ctx, lambda: None),
    )
    monkeypatch.setattr(
        run_loop_module,
        "_run_inner_loop",
        lambda _state, _ctx, _prev: (state_in, "complete", None),
    )

    marker_dir = tmp_path / ".ralph"

    class _SynchronousLateWatcher:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.heartbeat_client: _RecordingHeartbeat | None = None
            self.is_heartbeat_started = False
            self._stopped = False
            self._polls: list[bool] = []
            if not (marker_dir / "run.json").exists():
                self._polls.append(False)
            self._polls.append(True)

        def start(self) -> None:
            for marker_present in self._polls:
                if self._stopped:
                    return
                if marker_present:
                    recording = _RecordingHeartbeat()
                    recording.start()
                    self.heartbeat_client = recording
                    self.is_heartbeat_started = True
                    return

        def stop(self) -> None:
            self._stopped = True

    def _watcher_factory(_ws_root: Path) -> _SynchronousLateWatcher:
        return _SynchronousLateWatcher()

    hooks = ProPipelineHooks(marker_watcher_factory=_watcher_factory)
    exit_code = run_loop_module.run(config, initial_state=state_in, pro_hooks=hooks)
    assert exit_code == 0
