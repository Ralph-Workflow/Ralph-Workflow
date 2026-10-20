"""Pins E2.6: code changes mid-run; generation-dir interpreter + PYTHONPATH/_PACKAGE_ROOT ordering.

Catalogue requirement E2.6:
- Code under the child changes mid-run: The generation directory
  (~/.local/share/ralph-workflow-dev/generations/...) gets updated or garbage-collected,
  or Ralph is working on its own repository and the agent edits ralph/ in the workspace.
- Required: the candidate runs the same interpreter and the same code as the first child.
  Generation GC must never remove a generation a live run uses. The existing
  PYTHONPATH/_PACKAGE_ROOT ordering in _subprocess_env must be covered by a test in which
  the workspace contains a broken ralph/ package.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Pre-import workspace to prevent circular import between process.manager and workspace
from ralph import workspace as _workspace
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]
_ = _workspace


class _DummyProcess(ProcessLike):
    def __init__(self, pid: int = 6789) -> None:
        self._pid = pid
        self._poll: int | None = None

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self._poll = -9


def test_subprocess_env_sanitizes_broken_workspace_ralph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_subprocess_env replaces any PYTHONPATH entry providing a broken ralph package with _PACKAGE_ROOT."""
    broken_workspace = tmp_path / "broken_workspace"
    broken_pkg = broken_workspace / "ralph"
    broken_pkg.mkdir(parents=True, exist_ok=True)
    (broken_pkg / "__init__.py").write_text(
        "raise SyntaxError('broken workspace ralph package')\n",
        encoding="utf-8",
    )

    other_dir = tmp_path / "other_site_packages"
    other_dir.mkdir(parents=True, exist_ok=True)

    # Simulate ambient PYTHONPATH injected with the broken workspace path ahead of anything else
    ambient_pythonpath = f"{broken_workspace}{os.pathsep}{other_dir}"
    monkeypatch.setenv("PYTHONPATH", ambient_pythonpath)

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    env = lifecycle._subprocess_env(session_file)

    pythonpath_val = env.get("PYTHONPATH", "")
    entries = pythonpath_val.split(os.pathsep)

    # Invariant: The broken workspace path is completely sanitized out
    assert str(broken_workspace) not in entries

    # Invariant: _PACKAGE_ROOT comes first deterministically
    package_root_str = str(lifecycle._PACKAGE_ROOT)
    assert entries[0] == package_root_str

    # Invariant: No duplicate entries for _PACKAGE_ROOT
    assert entries.count(package_root_str) == 1

    # Invariant: Other non-ralph entries are preserved after _PACKAGE_ROOT
    assert str(other_dir) in entries


def test_candidate_spawn_uses_same_interpreter_as_parent(tmp_path: Path) -> None:
    """The candidate process is spawned using sys.executable (the generation-dir interpreter)."""
    spawned_commands: list[list[str]] = []

    def capture_spawn(
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del cwd, env, phase, pass_fds
        spawned_commands.append(list(command))
        return _DummyProcess()

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: session_file,
        subprocess_env=lifecycle._subprocess_env,
        spawn_process=capture_spawn,
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-e26-interp",
        drain="standalone",
    )

    _ = lifecycle._spawn_mcp_process(
        tmp_path,
        session,
        deps,
        None,
        None,
        [],
        port=9971,
        respawn=True,
        make_before_break=True,
    )

    assert len(spawned_commands) == 1
    cmd = spawned_commands[0]

    # Invariant: Command starts with sys.executable (same interpreter as parent)
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "ralph.mcp.server"]


def test_real_child_imports_package_root_despite_broken_ralph_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real subprocess running with _subprocess_env imports the genuine ralph package without error."""
    broken_workspace = tmp_path / "workspace"
    broken_pkg = broken_workspace / "ralph"
    broken_pkg.mkdir(parents=True, exist_ok=True)
    (broken_pkg / "__init__.py").write_text(
        "raise SyntaxError('FATAL: broken workspace package must not be imported!')\n",
        encoding="utf-8",
    )

    # Injected ambient PYTHONPATH
    monkeypatch.setenv("PYTHONPATH", str(broken_workspace))

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    env = lifecycle._subprocess_env(session_file)

    script_path = tmp_path / "test_import_real_child.py"
    script_path.write_text(
        "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "import ralph",
                "from ralph.mcp.server._start_stage_tracker import RUNTIME_STAGES",
                "assert len(RUNTIME_STAGES) > 0",
                "ralph_init = Path(ralph.__file__).resolve()",
                "expected_root = Path(sys.argv[1]).resolve()",
                "assert ralph_init.is_relative_to(expected_root), f'{ralph_init} not under {expected_root}'",
                "sys.exit(0)",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(script_path), str(lifecycle._PACKAGE_ROOT)],
        SpawnOptions(
            cwd=str(broken_workspace),
            env=env,
            label="e26-broken-pkg-test-child",
        ),
    )

    try:
        rc = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)

    assert rc == 0
    # Leak check
    assert proc.poll() is not None
