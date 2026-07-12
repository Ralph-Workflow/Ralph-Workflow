"""Regression tests for the ``SpawnOptions.stdin`` default and call-site contract.

When ``SpawnOptions().stdin`` defaults to ``None`` (the POSIX ``INHERIT``
constant for ``subprocess.Popen.stdin``), every child spawned through
``ProcessManager`` inherits Ralph's controlling-terminal stdin. An
interactive child (Claude Code, etc.) can then claim the foreground
process group, put the shared TTY into raw mode, and steal keystrokes.

The fix flips the dataclass default to ``subprocess.DEVNULL`` so
callers get a non-inheriting stdin by construction. The two call sites
that genuinely speak stdio (the MCP JSON-RPC transport paths) and the
two that already opted in to ``DEVNULL`` (agent subprocess paths) are
left unchanged because they pass ``stdin=`` explicitly.

Three tests:

1. ``test_spawn_options_defaults_to_devnull_stdin`` -- the dataclass
   default is ``subprocess.DEVNULL``, not ``None``.

2. ``test_no_spawn_call_site_passes_stdin_none`` -- AST-scan every
   ``SpawnOptions(...)`` call under ``ralph/`` for an explicit
   ``stdin=None`` keyword. Re-introducing INHERIT fails the audit.

3. ``test_process_manager_passes_devnull_when_caller_omits_stdin``
   -- drive BOTH the sync and async ``ProcessManager`` seams with a
   ``SpawnOptions()`` that omits ``stdin`` and assert the DEVNULL
   default reaches each factory boundary.

In-process, no real subprocess; stays under the per-test SIGALRM.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from ralph.process.manager import SpawnOptions
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.testing._fake_popen import FakePopen
from ralph.testing._process_state import ProcessState

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RALPH_PACKAGE_ROOT = _REPO_ROOT / "ralph"


def test_spawn_options_defaults_to_devnull_stdin() -> None:
    """The bare ``SpawnOptions()`` default must be ``subprocess.DEVNULL``.

    ``None`` (the pre-fix default) is the POSIX ``INHERIT`` constant for
    ``subprocess.Popen.stdin``; a child that inherits Ralph's
    controlling-terminal stdin can claim the foreground process group
    and put the shared TTY into raw mode, stealing keystrokes.

    The default must be the explicit ``subprocess.DEVNULL`` so EVERY
    child spawned through ProcessManager is non-inheriting by
    construction -- opt-in for stdin is only via
    ``SpawnOptions(stdin=subprocess.PIPE)``.
    """
    opts = SpawnOptions()
    assert opts.stdin is subprocess.DEVNULL, (
        f"SpawnOptions().stdin must default to subprocess.DEVNULL "
        f"(INHERIT leaks Ralph's controlling-terminal stdin); "
        f"got stdin={opts.stdin!r}"
    )


def test_no_spawn_call_site_passes_stdin_none() -> None:
    """No ``SpawnOptions(...)`` call site under ``ralph/`` may pass ``stdin=None``.

    AST-scans every Python file in the ralph/ package, locates every
    ``SpawnOptions(...)`` call, and asserts no call carries an
    explicit ``stdin`` keyword whose value is the constant ``None``.
    ``stdin=None`` means INHERIT -- the leak we are fixing.
    """
    offenders: list[str] = []
    # Fast-path text filter: skip files that don't even mention
    # ``SpawnOptions`` so the AST walk stays under the per-test SIGALRM
    # cap on a 1000+ file package.
    for source_path in _iter_python_files(_RALPH_PACKAGE_ROOT):
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "SpawnOptions" not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(source_path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_spawn_options_call(node):
                continue
            for kw in node.keywords:
                if kw.arg != "stdin":
                    continue
                if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                    offenders.append(_format_call_site(source_path, node))
    assert not offenders, (
        "SpawnOptions(stdin=None) re-introduced (would INHERIT Ralph's "
        "controlling-terminal stdin). Offending call sites:\n"
        + "\n".join(offenders)
    )


def test_process_manager_passes_devnull_when_caller_omits_stdin() -> None:
    """``SpawnOptions()`` must reach BOTH the sync and async factory as DEVNULL.

    The sync seam (ProcessManager.spawn) passes the whole ``SpawnOptions``
    OBJECT to the factory, so we assert on the captured object. The async
    seam (ProcessManager.spawn_async) destructures into kwargs before the
    factory call, so we assert on the ``stdin`` kwarg.

    Driving only one seam would leave the other unproven -- the sync path
    is used by the PTY-less invoke reader and the async path is used by
    the fan-out executor. Both must route DEVNULL through.
    """
    sync_captured: list[SpawnOptions] = []
    async_captured: list[dict[str, object]] = []

    def sync_factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command
        sync_captured.append(opts)
        return FakePopen(pid=1, state=ProcessState(returncode=0))

    async def async_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        del command, cwd, env, stdout, stderr
        async_captured.append(
            {
                "stdin": stdin,
                "start_new_session": start_new_session,
            }
        )
        # Return a fake whose awaited methods never run here -- the test
        # only inspects the captured kwargs and exits promptly.
        raise RuntimeError("async factory reached only to capture kwargs")

    sync_pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
        sync_process_factory=sync_factory,
    )

    sync_handle = sync_pm.spawn(["/usr/bin/true"])
    # Tear down via ``with`` so a failing assertion below cannot leave the
    # FakePopen-attached record wedged in ``sync_pm._records``.
    sync_handle.__exit__(None, None, None)
    assert len(sync_captured) == 1, (
        f"sync factory must be invoked exactly once; got {len(sync_captured)}"
    )
    sync_opts = sync_captured[0]
    assert sync_opts.stdin is subprocess.DEVNULL, (
        f"sync seam must pass DEVNULL when caller omits stdin; "
        f"got stdin={sync_opts.stdin!r}"
    )
    assert sync_opts.start_new_session is True, (
        f"sync seam must keep start_new_session=True; "
        f"got start_new_session={sync_opts.start_new_session!r}"
    )

    async_pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
        async_process_factory=async_factory,
    )
    # Drive spawn_async under asyncio -- only the kwargs capture matters.
    import asyncio

    try:
        asyncio.run(async_pm.spawn_async(["/usr/bin/true"]))
    except RuntimeError as exc:
        # The async factory raises after capturing kwargs -- that is the
        # expected exit path; any other exception is a test wiring bug.
        assert str(exc) == "async factory reached only to capture kwargs", (
            f"unexpected exception from async factory: {exc!r}"
        )

    assert len(async_captured) == 1, (
        f"async factory must be invoked exactly once; got {len(async_captured)}"
    )
    async_kwargs = async_captured[0]
    assert async_kwargs["stdin"] is subprocess.DEVNULL, (
        f"async seam must pass DEVNULL when caller omits stdin; "
        f"got stdin={async_kwargs['stdin']!r}"
    )
    assert async_kwargs["start_new_session"] is True, (
        f"async seam must keep start_new_session=True; "
        f"got start_new_session={async_kwargs['start_new_session']!r}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _iter_python_files(root: Path) -> Iterator[Path]:
    """Yield every ``*.py`` file under ``root`` (recursive)."""
    yield from sorted(root.rglob("*.py"))


def _is_spawn_options_call(node: ast.Call) -> bool:
    """Return True when the call's function is the ``SpawnOptions`` name."""
    func = node.func
    return (
        (isinstance(func, ast.Name) and func.id == "SpawnOptions")
        or (isinstance(func, ast.Attribute) and func.attr == "SpawnOptions")
    )


def _format_call_site(path: Path, node: ast.Call) -> str:
    """Return a one-line report of a violation: ``path:lineno: snippet``."""
    try:
        segment = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    except (OSError, SyntaxError):
        segment = "<source unavailable>"
    # Strip newlines so the violation is a single line.
    snippet = segment.replace("\n", " ").strip()
    return f"{path}:{node.lineno}: {snippet}"
