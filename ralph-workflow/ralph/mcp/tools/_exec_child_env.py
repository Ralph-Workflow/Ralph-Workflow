from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.agents.invoke.agent_search_env import apply_safe_search_env
from ralph.process._spawn_env import scrub_activity_relay_controls

if TYPE_CHECKING:
    from pathlib import Path


def exec_child_env(cwd: Path, tmpdir: Path | None = None) -> dict[str, str]:
    env = scrub_activity_relay_controls(dict(os.environ))
    env.pop("RALPH_BROKER_SECRET", None)
    env["PWD"] = str(cwd)
    env.pop("OLDPWD", None)
    if tmpdir is not None:
        env.update({"TMPDIR": str(tmpdir), "TMP": str(tmpdir), "TEMP": str(tmpdir)})
    apply_safe_search_env(env)
    scrub_activity_relay_controls(env)
    return env


__all__ = ["exec_child_env"]
