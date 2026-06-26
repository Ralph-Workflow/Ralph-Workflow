"""Black-box tests for ``run_git`` ``output_limit_bytes`` plumbing.

wt-024 memory-perf AC-07: git output capture was unbounded \u2014 a massive
``git log`` / ``git diff`` / ``git status`` would buffer the entire
payload in memory. We add a ``GIT_OUTPUT_LIMIT_BYTES`` default constant
(10 MiB, matching the existing ``SPILL_OUTPUT_LIMIT_BYTES`` precedent)
and plumb an ``output_limit_bytes`` field through ``GitRunOptions`` to
``proc.communicate_and_cleanup``.

When ``output_limit_bytes`` is None the existing unbounded behavior is
preserved exactly (backward compat). When set, ``_communicate_with_output_limit``
truncates stdout at the cap with a marker (existing
``ManagedProcessOutputLimitExceededError`` semantics).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.process.manager._managed_process import ManagedProcessOutputLimitExceededError
from ralph.timeout_defaults import GIT_OUTPUT_LIMIT_BYTES


class _FakeProc:
    """A fake ManagedProcess that records ``communicate_and_cleanup`` args."""

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.communicate_calls: list[dict[str, object]] = []
        self.terminated = False
        self.communicate_raises: BaseException | None = None
        # The production code reads proc.stdout / proc.stderr to close them.
        self.stdout = None
        self.stderr = None
        # The production code accesses proc._proc for the raw Popen.
        self._proc = MagicMock()

    def communicate_and_cleanup(self, **kwargs: object) -> tuple[bytes, bytes]:
        self.communicate_calls.append(kwargs)
        if self.communicate_raises is not None:
            raise self.communicate_raises
        return self._stdout, self._stderr

    @property
    def returncode(self) -> int:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode

    def terminate(self, grace_period_s: float | None = None) -> None:
        self.terminated = True


@pytest.fixture
def fake_proc_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeProc:
    """Install a fake ProcessManager.spawn that returns a _FakeProc."""
    fake = _FakeProc()

    def fake_spawn(cmd: object, opts: object, *, label: object = None) -> _FakeProc:
        return fake

    fake_pm = MagicMock()
    fake_pm.spawn = fake_spawn
    monkeypatch.setattr(
        "ralph.git.subprocess_runner.get_process_manager",
        lambda: fake_pm,
    )
    return fake


def test_output_limit_bytes_field_default_is_none() -> None:
    """``GitRunOptions.output_limit_bytes`` defaults to ``None`` (backward compat)."""
    opts = GitRunOptions()
    assert opts.output_limit_bytes is None, (
        f"output_limit_bytes default must be None; got {opts.output_limit_bytes!r}"
    )


def test_run_git_passes_output_limit_bytes_through(fake_proc_factory: _FakeProc) -> None:
    """``run_git`` must forward ``output_limit_bytes`` to ``communicate_and_cleanup``."""
    run_git(
        ["status"],
        cwd=Path("/tmp"),
        label="git-status",
        options=GitRunOptions(output_limit_bytes=10 * 1024 * 1024),
    )
    assert fake_proc_factory.communicate_calls, "communicate_and_cleanup must be called"
    forwarded = fake_proc_factory.communicate_calls[0]
    assert forwarded.get("output_limit_bytes") == 10 * 1024 * 1024, (
        f"output_limit_bytes must be forwarded to communicate_and_cleanup; "
        f"got {forwarded.get('output_limit_bytes')!r}"
    )


def test_run_git_unlimited_when_output_limit_bytes_is_none(
    fake_proc_factory: _FakeProc,
) -> None:
    """When ``output_limit_bytes`` is None, run_git must pass None through."""
    run_git(
        ["status"],
        cwd=Path("/tmp"),
        label="git-status",
        options=GitRunOptions(output_limit_bytes=None),
    )
    forwarded = fake_proc_factory.communicate_calls[0]
    assert "output_limit_bytes" in forwarded, (
        "run_git must explicitly forward output_limit_bytes (even when None) "
        "so the bounded branch in communicate_and_cleanup is unambiguously "
        "opt-in"
    )
    assert forwarded["output_limit_bytes"] is None


def test_run_git_handles_output_limit_exceeded(fake_proc_factory: _FakeProc) -> None:
    """``ManagedProcessOutputLimitExceededError`` from ``communicate_and_cleanup``
    must propagate; the proc must be terminated."""
    fake_proc_factory.communicate_raises = ManagedProcessOutputLimitExceededError(
        output_limit_bytes=1024, stdout=b"", stderr=b""
    )
    with pytest.raises(ManagedProcessOutputLimitExceededError):
        run_git(
            ["log"],
            cwd=Path("/tmp"),
            label="git-log",
            options=GitRunOptions(output_limit_bytes=1024),
        )
    assert fake_proc_factory.terminated, (
        "the proc must be terminated when communicate_and_cleanup raises"
    )


def test_git_output_limit_bytes_constant_value() -> None:
    """``GIT_OUTPUT_LIMIT_BYTES`` must be a positive integer >= 1 MiB."""
    assert isinstance(GIT_OUTPUT_LIMIT_BYTES, int)
    assert GIT_OUTPUT_LIMIT_BYTES >= 1024 * 1024, (
        f"GIT_OUTPUT_LIMIT_BYTES must be >= 1 MiB; got {GIT_OUTPUT_LIMIT_BYTES}"
    )
