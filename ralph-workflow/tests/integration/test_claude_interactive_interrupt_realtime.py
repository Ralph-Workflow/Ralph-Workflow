from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from ralph.interrupt.controller import INTERRUPT_EXIT_CODE

pytestmark = pytest.mark.subprocess_e2e

PYTHON = sys.executable
REPO_ROOT = Path(__file__).resolve().parents[2]


def _pid_gone(pid: int, timeout_s: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.02)
    return not psutil.pid_exists(pid)


@pytest.mark.timeout_seconds(8)
def test_live_sigint_terminates_pty_backed_interactive_claude(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "pty-interrupt.json"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude_interactive_pty.py"
    script = textwrap.dedent(
        """
        import json
        import os
        import signal
        import sys
        import threading
        from pathlib import Path
        from unittest.mock import MagicMock

        import ralph.process.manager as _mgr
        from ralph.agents import invoke as invoke_module
        from ralph.agents.invoke import InvokeOptions
        from ralph.config.enums import AgentTransport, JsonParserType, Verbosity
        from ralph.config.models import AgentConfig
        from ralph.pipeline import runner as runner_module
        from ralph.pipeline.effects import InvokeAgentEffect
        from ralph.process.manager import ProcessManager, ProcessManagerPolicy
        from ralph.workspace.scope import WorkspaceScope

        workspace = Path(sys.argv[1])
        fixture = Path(sys.argv[2])
        result_path = Path(sys.argv[3])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".agent").mkdir(exist_ok=True)
        prompt_file = workspace / "PROMPT.md"
        prompt_file.write_text("Exercise PTY interrupt handling.", encoding="utf-8")

        pm = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
                enable_zombie_reaper=False,
            )
        )

        claude_config = AgentConfig(
            cmd=f"{sys.executable} {fixture} --sleep",
            output_flag=None,
            yolo_flag=None,
            json_parser=JsonParserType.CLAUDE,
            session_flag="--resume {}",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        )

        def fake_execute_agent_effect(effect, config, deps, workspace_scope, **kwargs):
            del effect, config, deps, workspace_scope, kwargs
            for _line in invoke_module.invoke_agent(
                claude_config,
                str(prompt_file),
                options=InvokeOptions(workspace_path=workspace, show_progress=False),
            ):
                pass
            return 0

        def stub_determine(state, policy_bundle, workspace_scope=None, config=None):
            del state, policy_bundle, workspace_scope, config
            return InvokeAgentEffect(
                agent_name="claude",
                phase="development",
                prompt_file=str(prompt_file),
            )

        runner_module.resolve_workspace_scope = lambda: WorkspaceScope(workspace)
        runner_module.write_start_commit_if_absent = lambda _: None
        runner_module.validate_custom_mcp_servers = lambda _: 0
        mock_bundle = MagicMock()
        mock_bundle.pipeline.terminal_phase = "complete"
        runner_module.load_policy_or_die = lambda *args, **kwargs: mock_bundle
        runner_module.AgentRegistry = MagicMock()
        runner_module.call_determine_effect_from_policy = stub_determine
        runner_module.execute_agent_effect = fake_execute_agent_effect
        runner_module.materialize_agent_prompt_if_needed = lambda *args, **kwargs: None
        runner_module.phase_event_after_agent_run = lambda **kwargs: 0
        runner_module.ckpt.save = lambda state, *_args, **_kwargs: None

        initial_state = MagicMock()
        initial_state.phase = "development"
        initial_state.recovery_epoch = 0
        interrupted_state = MagicMock()
        interrupted_state.phase = "development"
        initial_state.copy_with.return_value = interrupted_state

        original_singleton = _mgr._pm_state.instance
        _mgr._pm_state.instance = pm
        try:
            threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            exit_code = runner_module.run(
                MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET
            )
            records = [
                {"pid": record.pid, "status": record.status.value, "returncode": record.returncode}
                for record in pm.list_records(include_active=False, include_terminal=True)
                if record.label and record.label.startswith("invoke:")
            ]
            result_path.write_text(
                json.dumps({"runner_exit_code": exit_code, "records": records}),
                encoding="utf-8",
            )
        finally:
            _mgr._pm_state.instance = original_singleton
        """
    )

    completed = subprocess.run(
        [PYTHON, "-c", script, str(workspace), str(fixture), str(result_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["runner_exit_code"] == INTERRUPT_EXIT_CODE
    assert payload["records"], payload
    for record in payload["records"]:
        assert _pid_gone(record["pid"]), f"PTY child {record['pid']} must be gone after SIGINT"
