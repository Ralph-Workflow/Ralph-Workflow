from __future__ import annotations

import inspect

import ralph.executor.process as _executor_process
import ralph.git.subprocess_runner as _subprocess_runner
import ralph.mcp.server.lifecycle as _lifecycle
import ralph.pipeline.parallel.coordinator as _coordinator
import ralph.process.manager as _manager

_POSIX_FORBIDDEN = (
    "os.killpg(",
    "os.setsid(",
    "signal.SIGTERM",
    "signal.SIGKILL",
    "os.setpgrp(",
)

_CHECKED_MODULES = (
    _manager,
    _executor_process,
    _subprocess_runner,
    _lifecycle,
    _coordinator,
)


def test_no_posix_only_apis_in_process_manager_and_callers() -> None:
    violations = [
        f"{mod.__name__}: contains '{token}'"
        for mod in _CHECKED_MODULES
        for token in _POSIX_FORBIDDEN
        if token in inspect.getsource(mod)
    ]

    assert not violations, "POSIX-only APIs found — replace with psutil-based logic:\n" + "\n".join(
        violations
    )
