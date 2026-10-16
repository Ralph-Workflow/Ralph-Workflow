"""Regression tests for the ``SpawnOptions.pass_fds`` field and forwarding.

``pass_fds`` is the ``subprocess.Popen`` kwarg for keeping specific file
descriptors inherited by the child across spawn. The dataclass default is
``()`` (no extra FDs inherited) and ``_default_sync_process_factory``
must forward whatever the caller passes straight through to
``subprocess.Popen`` as the ``pass_fds=`` keyword.

Two tests:

1. ``test_spawn_options_pass_fds_default_is_empty_tuple`` -- the bare
   ``SpawnOptions()`` default is the empty tuple, not ``None``.

2. ``test_default_sync_process_factory_forwards_pass_fds_to_popen`` -- the
   sync factory threads ``opts.pass_fds`` into the Popen call. Captured
   via a monkey-patched ``subprocess.Popen`` (the canonical import site
   the factory uses), so no real subprocess is spawned.

In-process, no real I/O, no real subprocess; stays under the per-test
SIGALRM.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from ralph.process.manager import _default_sync_process_factory
from ralph.process.manager._spawn_options import SpawnOptions

if TYPE_CHECKING:
    import pytest


def test_spawn_options_pass_fds_default_is_empty_tuple() -> None:
    """The bare ``SpawnOptions()`` default must be the empty tuple.

    ``pass_fds`` carries FD numbers to inherit into the child. Popen's own
    default is ``()``; keeping the dataclass default in sync means the
    factory does not have to special-case the unset path.
    """
    opts = SpawnOptions()
    assert opts.pass_fds == (), (
        f"SpawnOptions().pass_fds must default to (); got pass_fds={opts.pass_fds!r}"
    )


def test_default_sync_process_factory_forwards_pass_fds_to_popen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync factory must forward ``opts.pass_fds`` to ``subprocess.Popen``.

    Driven with ``SpawnOptions(pass_fds=(4, 5))``; the Popen call is
    intercepted via a monkey-patched ``subprocess.Popen`` (the same
    module the factory accesses through). The captured kwargs must show
    ``pass_fds=(4, 5)`` -- any other value means the factory dropped or
    mutated the field on its way to the kernel-facing boundary.
    """
    captured: list[dict[str, Any]] = []

    class _CapturePopen:
        """Capture-only Popen stub. No real subprocess is spawned."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.append({"args": args, "kwargs": kwargs})
            self.pid = 0

    monkeypatch.setattr(subprocess, "Popen", _CapturePopen)

    _default_sync_process_factory(["/bin/true"], SpawnOptions(pass_fds=(4, 5)))

    assert len(captured) == 1, (
        f"subprocess.Popen must be invoked exactly once; got {len(captured)}"
    )
    popen_kwargs = captured[0]["kwargs"]
    assert popen_kwargs.get("pass_fds") == (4, 5), (
        f"sync factory must forward pass_fds to Popen unchanged; "
        f"got pass_fds={popen_kwargs.get('pass_fds')!r}"
    )
