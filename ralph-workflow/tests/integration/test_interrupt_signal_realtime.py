from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

INTERRUPT_EXIT_CODE = 130
PYTHON = sys.executable
REPO_ROOT = Path(__file__).resolve().parents[2]
_STUBBORN_CHILD = (
    "import signal, time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "time.sleep(30)"
)


def _pid_gone(pid: int, timeout_s: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.02)
    return not psutil.pid_exists(pid)


@pytest.mark.timeout_seconds(5)
def test_live_sigint_gracefully_terminates_runner_and_tracked_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "interrupt-result.json"
    script = textwrap.dedent(
        """
        import json
        import os
        import signal
        import sys
        import threading
        import time
        from pathlib import Path
        from unittest.mock import MagicMock

        import ralph.process.manager as _mgr
        from ralph.config.enums import Verbosity
        from ralph.pipeline import runner as runner_module
        from ralph.pipeline.effects import InvokeAgentEffect
        from ralph.process.manager import ProcessManager, ProcessManagerPolicy, get_process_manager
        from ralph.workspace.scope import WorkspaceScope

        workspace = Path(sys.argv[1])
        result_path = Path(sys.argv[2])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".agent").mkdir(exist_ok=True)

        complete_state = MagicMock()
        complete_state.phase = "complete"
        spawned_pid = []
        pm = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
            )
        )

        def fake_execute_agent_effect(
            effect, config, deps, workspace_scope, **kwargs
        ):
            handle = get_process_manager().spawn(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                label="invoke:fake-agent",
            )
            spawned_pid.append(handle.record.pid)
            time.sleep(30)
            return 0

        def stub_determine(state, policy_bundle, workspace_scope=None, config=None):
            return InvokeAgentEffect(
                agent_name="fake-agent",
                phase="fake-phase",
                prompt_file="/dev/null",
            )

        runner_module.resolve_workspace_scope = lambda: WorkspaceScope(workspace)
        runner_module._write_start_commit_if_absent = lambda _: None
        runner_module._validate_custom_mcp_servers = lambda _: 0
        mock_bundle = MagicMock()
        mock_bundle.pipeline.terminal_phase = "complete"
        runner_module.load_policy_or_die = lambda *args, **kwargs: mock_bundle
        runner_module.AgentRegistry = MagicMock()
        runner_module._call_determine_effect_from_policy = stub_determine
        runner_module._execute_agent_effect = fake_execute_agent_effect
        runner_module._materialize_agent_prompt_if_needed = lambda *args, **kwargs: None
        runner_module._phase_event_after_agent_run = lambda **kwargs: 0
        runner_module.ckpt.save = lambda state: None

        initial_state = MagicMock()
        initial_state.phase = "fake-phase"
        initial_state.recovery_epoch = 0
        interrupted_state = MagicMock()
        interrupted_state.phase = "fake-phase"
        initial_state.copy_with.return_value = interrupted_state

        original_singleton = _mgr._singleton
        _mgr._singleton = pm
        try:
            threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            exit_code = runner_module.run(
                MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET
            )
            result_path.write_text(
                json.dumps({"runner_exit_code": exit_code, "child_pid": spawned_pid[0]}),
                encoding="utf-8",
            )
        finally:
            _mgr._singleton = original_singleton
        """
    )

    completed = subprocess.run(
        [PYTHON, "-c", script, str(workspace), str(result_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["runner_exit_code"] == INTERRUPT_EXIT_CODE
    assert _pid_gone(payload["child_pid"]), (
        f"Tracked PID {payload['child_pid']} must be gone after live SIGINT handling"
    )


@pytest.mark.timeout_seconds(5)
def test_second_live_sigint_force_kills_stubborn_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-second"
    workspace.mkdir()
    script = textwrap.dedent(
        f"""
        import os
        import signal
        import sys
        import threading
        import time
        from pathlib import Path
        from unittest.mock import MagicMock

        import ralph.process.manager as _mgr
        from ralph.config.enums import Verbosity
        from ralph.pipeline import runner as runner_module
        from ralph.pipeline.effects import InvokeAgentEffect
        from ralph.process.manager import ProcessManager, ProcessManagerPolicy, get_process_manager
        from ralph.workspace.scope import WorkspaceScope

        workspace = Path(sys.argv[1])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".agent").mkdir(exist_ok=True)
        stubborn_child = {_STUBBORN_CHILD!r}

        pm = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=10.0,
                kill_followup_timeout_s=0.2,
                log_events=False,
            )
        )

        def fake_execute_agent_effect(
            effect, config, deps, workspace_scope, **kwargs
        ):
            handle = get_process_manager().spawn(
                [
                    sys.executable,
                    "-c",
                    stubborn_child,
                ],
                label="invoke:fake-agent",
            )
            print(f"CHILD_PID={{handle.record.pid}}", flush=True)
            time.sleep(30)
            return 0

        def stub_determine(state, policy_bundle, workspace_scope=None, config=None):
            return InvokeAgentEffect(
                agent_name="fake-agent",
                phase="fake-phase",
                prompt_file="/dev/null",
            )

        runner_module.resolve_workspace_scope = lambda: WorkspaceScope(workspace)
        runner_module._write_start_commit_if_absent = lambda _: None
        runner_module._validate_custom_mcp_servers = lambda _: 0
        mock_bundle = MagicMock()
        mock_bundle.pipeline.terminal_phase = "complete"
        runner_module.load_policy_or_die = lambda *args, **kwargs: mock_bundle
        runner_module.AgentRegistry = MagicMock()
        runner_module._call_determine_effect_from_policy = stub_determine
        runner_module._execute_agent_effect = fake_execute_agent_effect
        runner_module._materialize_agent_prompt_if_needed = lambda *args, **kwargs: None
        runner_module._phase_event_after_agent_run = lambda **kwargs: 0
        runner_module.ckpt.save = lambda state: None

        initial_state = MagicMock()
        initial_state.phase = "fake-phase"
        initial_state.recovery_epoch = 0
        interrupted_state = MagicMock()
        interrupted_state.phase = "fake-phase"
        initial_state.copy_with.return_value = interrupted_state

        original_singleton = _mgr._singleton
        _mgr._singleton = pm
        try:
            threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            runner_module.run(MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET)
        finally:
            _mgr._singleton = original_singleton
        """
    )

    completed = subprocess.run(
        [PYTHON, "-c", script, str(workspace)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )

    child_pid_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith("CHILD_PID=")),
        None,
    )
    assert child_pid_line is not None, completed.stdout
    child_pid = int(child_pid_line.split("=", 1)[1])

    assert completed.returncode == INTERRUPT_EXIT_CODE, completed.stderr
    assert _pid_gone(child_pid), (
        f"Tracked PID {child_pid} must be gone after second-SIGINT forced termination"
    )
