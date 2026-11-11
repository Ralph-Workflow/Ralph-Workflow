from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from ralph.process.manager import SpawnOptions, default_sync_process_factory

if TYPE_CHECKING:
    import pytest


def test_spawn_options_pass_fds_default_is_empty_tuple() -> None:
    opts = SpawnOptions()
    assert opts.pass_fds == (), (
        f"SpawnOptions().pass_fds must default to (); got pass_fds={opts.pass_fds!r}"
    )


def test_default_sync_process_factory_forwards_pass_fds_to_popen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    class _CapturePopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.append({"args": args, "kwargs": kwargs})
            self.pid = 0

    monkeypatch.setattr(subprocess, "Popen", _CapturePopen)

    default_sync_process_factory(["/bin/true"], SpawnOptions(pass_fds=(4, 5)))

    assert len(captured) == 1, f"subprocess.Popen must be invoked exactly once; got {len(captured)}"
    popen_kwargs = captured[0]["kwargs"]
    assert popen_kwargs.get("pass_fds") == (4, 5), (
        f"sync factory must forward pass_fds to Popen unchanged; "
        f"got pass_fds={popen_kwargs.get('pass_fds')!r}"
    )
