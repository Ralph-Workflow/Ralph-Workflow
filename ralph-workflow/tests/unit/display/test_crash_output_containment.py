"""Regression tests for crash-output terminal containment (S-4)."""

from __future__ import annotations

from io import StringIO

from ralph.display import excepthook


def _reset_state() -> None:
    excepthook._STATE.installed = False


def test_crash_output_regression_sanitizes_terminal_control_and_restores_terminal() -> None:
    """S-4: uncaught main-thread errors cannot repaint or leave the TTY altered."""
    _reset_state()
    stderr = StringIO()
    installed: list[object] = []
    restores: list[None] = []

    def restore() -> None:
        restores.append(None)

    excepthook.install_sanitizing_excepthook(
        stderr=stderr,
        sys_setter=installed.append,
        thread_setter=installed.append,
        restore=restore,
    )

    assert len(installed) == 2
    hook = installed[0]
    assert callable(hook)
    error = RuntimeError("agent bytes: \x1b[?1003h\x1b[?1006h\x1b[?25l\x1b[?1049h")
    hook(RuntimeError, error, error.__traceback__)

    rendered = stderr.getvalue()
    assert "agent bytes:" in rendered
    assert "\x1b[?1003h" not in rendered
    assert "\x1b[?1006h" not in rendered
    assert "\x1b[?25l" not in rendered
    assert "\x1b[?1049h" not in rendered
    assert restores == [None]


def test_worker_crash_regression_uses_same_sanitized_restore_boundary() -> None:
    """S-4: worker-thread crashes cannot bypass terminal restoration."""
    _reset_state()
    stderr = StringIO()
    installed: list[object] = []
    restores: list[None] = []

    def restore() -> None:
        restores.append(None)

    excepthook.install_sanitizing_excepthook(
        stderr=stderr,
        sys_setter=installed.append,
        thread_setter=installed.append,
        restore=restore,
    )

    thread_hook = installed[1]
    assert callable(thread_hook)

    class _Args:
        exc_type = RuntimeError
        exc_value = RuntimeError("worker: \x1b[?1003h\x1b[?25l")
        exc_traceback = None

    thread_hook(_Args())
    assert "worker:" in stderr.getvalue()
    assert "\x1b" not in stderr.getvalue()
    assert restores == [None]
