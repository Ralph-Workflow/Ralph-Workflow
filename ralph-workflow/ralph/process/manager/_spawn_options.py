from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class SpawnOptions:
    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: int | None = subprocess.DEVNULL
    stdout: int | None = None
    stderr: int | None = None
    start_new_session: bool = True
    label: str | None = None
    text: bool = False
    errors: str = "replace"
    allow_activity_relay_controls: bool = False
    allow_broker_secret: bool = False
    pass_fds: tuple[int, ...] = ()
    require_direct_parent: bool = False
