from __future__ import annotations

import ast
import os
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.testing import FakePopen
from ralph.testing.fake_process import ProcessState

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RALPH_PACKAGE_ROOT = _REPO_ROOT / "ralph"


def test_spawn_options_defaults_to_devnull_stdin() -> None:
    opts = SpawnOptions()
    assert opts.stdin is subprocess.DEVNULL, (
        f"SpawnOptions().stdin must default to subprocess.DEVNULL "
        f"(INHERIT leaks Ralph's controlling-terminal stdin); "
        f"got stdin={opts.stdin!r}"
    )


def test_no_spawn_call_site_passes_stdin_none() -> None:
    offenders: list[str] = []
    for source_path in _iter_python_files(_RALPH_PACKAGE_ROOT):
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "SpawnOptions" not in source or "stdin=None" not in source:
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
        "controlling-terminal stdin). Offending call sites:\n" + "\n".join(offenders)
    )


def test_process_manager_passes_devnull_when_caller_omits_stdin() -> None:
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
        raise RuntimeError("async factory reached only to capture kwargs")

    sync_pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
        sync_process_factory=sync_factory,
    )

    sync_handle = sync_pm.spawn(["/usr/bin/true"])
    sync_handle.__exit__(None, None, None)
    assert len(sync_captured) == 1, (
        f"sync factory must be invoked exactly once; got {len(sync_captured)}"
    )
    sync_opts = sync_captured[0]
    assert sync_opts.stdin is subprocess.DEVNULL, (
        f"sync seam must pass DEVNULL when caller omits stdin; got stdin={sync_opts.stdin!r}"
    )
    assert sync_opts.start_new_session is True, (
        f"sync seam must keep start_new_session=True; "
        f"got start_new_session={sync_opts.start_new_session!r}"
    )

    async_pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
        async_process_factory=async_factory,
    )
    import asyncio

    try:
        asyncio.run(async_pm.spawn_async(["/usr/bin/true"]))
    except RuntimeError as exc:
        assert str(exc) == "async factory reached only to capture kwargs", (
            f"unexpected exception from async factory: {exc!r}"
        )

    assert len(async_captured) == 1, (
        f"async factory must be invoked exactly once; got {len(async_captured)}"
    )
    async_kwargs = async_captured[0]
    assert async_kwargs["stdin"] is subprocess.DEVNULL, (
        f"async seam must pass DEVNULL when caller omits stdin; got stdin={async_kwargs['stdin']!r}"
    )
    assert async_kwargs["start_new_session"] is True, (
        f"async seam must keep start_new_session=True; "
        f"got start_new_session={async_kwargs['start_new_session']!r}"
    )


def _iter_python_files(root: Path) -> Iterator[Path]:
    for directory, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(".py"):
                yield Path(directory, name)


def _is_spawn_options_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "SpawnOptions") or (
        isinstance(func, ast.Attribute) and func.attr == "SpawnOptions"
    )


def _format_call_site(path: Path, node: ast.Call) -> str:
    try:
        segment = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    except (OSError, SyntaxError):
        segment = "<source unavailable>"
    snippet = segment.replace("\n", " ").strip()
    return f"{path}:{node.lineno}: {snippet}"
