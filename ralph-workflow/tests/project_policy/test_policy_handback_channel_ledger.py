from __future__ import annotations

import json
import os
import select
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from rich.live import Live

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline._runner_session import (
    pop_last_captured_session_id,
)
from ralph.pipeline._runner_session import (
    set_last_captured_session_id as _set_session_id,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps


def _seed_analysis_approval(ws: FsWorkspace) -> None:
    from ralph.project_policy import analysis as policy_analysis
    from ralph.project_policy import markers as pp_markers

    ws.mkdirs(f"{pp_markers.CACHE_REL_PATH.rsplit('/', 1)[0]}/artifacts")
    ws.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        json.dumps(
            {
                "type": policy_analysis.ANALYSIS_ARTIFACT_TYPE,
                "content": {"status": "completed", "summary": "policy is sound"},
            }
        ),
    )


def _build_load_result(
    workspace_root: Path,
    *,
    policy_bundle: object | None = None,
) -> run_module._LoadResult:
    return run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root=str(workspace_root),
            allowed_roots=[str(workspace_root)],
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=policy_bundle,
        run_id="test-run-id",
    )


def _spawn_pty_drained_console() -> tuple[Console, int, list[bytes], threading.Event]:
    master_fd, slave_fd = os.openpty()
    os.set_blocking(master_fd, False)
    slave_file = os.fdopen(slave_fd, "w")
    console = Console(file=slave_file, force_terminal=True)
    accumulated: list[bytes] = []
    stop_event = threading.Event()

    def _drain() -> None:
        while not stop_event.is_set():
            try:
                readable, _, _ = select.select([master_fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except (BlockingIOError, OSError):
                continue
            if not chunk:
                continue
            accumulated.append(chunk)

    threading.Thread(target=_drain, name="pytest-pty-drain", daemon=True).start()
    return console, master_fd, accumulated, stop_event


def _close_pty(
    console: Console,
    master_fd: int,
    stop_event: threading.Event,
) -> None:
    stop_event.set()
    try:
        os.set_blocking(master_fd, False)
        while True:
            try:
                _chunk = os.read(master_fd, 4096)
            except (BlockingIOError, OSError):
                break
            if not _chunk:
                break
    finally:
        with suppress(OSError):
            console.file.close()
        with suppress(OSError):
            os.close(master_fd)


def _drain_then_snapshot_threads(preflight_threads: set[int]) -> list[str]:
    for _ in range(40):
        live_threads = [t for t in threading.enumerate() if t.ident is not None]
        new_live = [t for t in live_threads if t.ident not in preflight_threads]
        if not new_live:
            break
        if all(not t.is_alive() for t in new_live):
            break
        drain_signal: threading.Event = threading.Event()
        drain_signal.wait(timeout=0.025)
    return sorted(
        t.name
        for t in threading.enumerate()
        if t.ident is not None and t.ident not in preflight_threads
    )


def _probe_console_live(console: Console) -> str:
    try:
        with Live("", console=console, transient=False):
            pass
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"
    return "ok"


def _blocking_policy_paths() -> set[str]:
    from ralph.project_policy import markers

    paths: set[str] = {
        markers.AGENTS_MD,
        markers.CLAUDE_MD,
    }
    paths.update(f"{markers.CANONICAL_DIR}{name}" for name in markers.CORE_POLICY_FILES)
    return paths


@pytest.mark.timeout_seconds(20)
def test_post_preflight_channel_ledger_records_six_channels(
    tmp_git_repo: Path,
) -> None:
    bundle = load_policy(default_dir())
    workspace = FsWorkspace(tmp_git_repo, allowed_roots=[tmp_git_repo])

    _set_session_id(None)

    before_dirty = list_dirty_paths(tmp_git_repo)

    console, master_fd, accumulated, stop_event = _spawn_pty_drained_console()
    display_context = make_display_context(console=console)
    load_result = _build_load_result(tmp_git_repo, policy_bundle=bundle)

    preflight_threads: set[int] = {t.ident for t in threading.enumerate() if t.ident is not None}

    def failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, workspace_scope, args, opts
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = failing_execute_agent_effect
    try:
        rc = cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            workspace_factory=lambda: workspace,
            emit_factory=lambda _m: None,
            is_tty=lambda: False,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor

    console_live_value = _probe_console_live(console)

    _close_pty(console, master_fd, stop_event)
    for _ in range(20):
        if not any(t.name == "pytest-pty-drain" for t in threading.enumerate()):
            break
        drain_signal: threading.Event = threading.Event()
        drain_signal.wait(timeout=0.05)

    leaked_session_id = pop_last_captured_session_id()

    transcript = b"".join(accumulated).decode("utf-8", errors="replace")
    remediation_label_leaked = "Remediation 1" in transcript

    new_thread_names = _drain_then_snapshot_threads(preflight_threads)

    from ralph.language_detector import get_project_stack
    from ralph.project_policy.preflight import run_policy_readiness_preflight

    second_agent_invocations: list[str] = []

    def second_failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, workspace_scope, args, opts
        second_agent_invocations.append(effect.phase)
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor_2 = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = second_failing_execute_agent_effect
    try:
        second_stack = get_project_stack(workspace)
        run_policy_readiness_preflight(
            workspace,
            second_stack,
            emit=lambda _m: None,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor_2

    after_dirty = list_dirty_paths(tmp_git_repo)

    policy_paths = _blocking_policy_paths()

    print(f"channel=capture before=None after={leaked_session_id!r}")
    print(f"channel=console_live before=ok after={console_live_value}")
    print(
        f"channel=footer_bytes before=neutral"
        f" after={'leaked' if remediation_label_leaked else 'neutral'}"
    )
    print(f"channel=threads before=[] after={new_thread_names!r}")
    print(f"channel=second_preflight before=0 after={len(second_agent_invocations)}")
    added_dirty = sorted(after_dirty - before_dirty)
    policy_dirty = sorted(path for path in added_dirty if path in policy_paths)
    print(f"channel=dirty_tree before={sorted(before_dirty)} after={policy_dirty}")

    assert rc == 0, "preflight should exit 0 even on the BLOCKED hand-back"
    assert leaked_session_id is None, (
        f"capture channel: post-preflight session id {leaked_session_id!r} not drained"
    )
    assert console_live_value == "ok", (
        f"console_live channel: dead display still owns the console {console_live_value!r}"
    )
    assert new_thread_names == [], (
        f"threads channel: preflight created threads {new_thread_names!r}"
    )
    assert not policy_dirty, (
        "dirty_tree channel: BLOCKED hand-back left policy-scope paths dirty: "
        f"{sorted(policy_dirty)!r}. The S-4 fix should sweep the deterministic "
        "chore commit onto the BLOCKED route -- see "
        "cli_integration._dispatch_preflight_result."
    )
