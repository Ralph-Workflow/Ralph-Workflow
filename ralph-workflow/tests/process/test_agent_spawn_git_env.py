from __future__ import annotations

import pytest

from ralph.git.hardening import GIT_DIR_ENV_KEYS
from ralph.process import child_env_for_spawn

pytestmark = pytest.mark.timeout_seconds(5)


def test_a_spawned_agent_never_inherits_a_repository_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in GIT_DIR_ENV_KEYS:
        monkeypatch.setenv(key, f"/repo/.git/worktrees/wt/{key}")

    inherited = child_env_for_spawn(None, cwd="/repo/wt")
    explicit = child_env_for_spawn(
        {"GIT_DIR": "/repo/.git/worktrees/wt", "GIT_COMMON_DIR": "/repo/.git", "PATH": "/usr/bin"}
    )

    assert not GIT_DIR_ENV_KEYS & inherited.keys()
    assert explicit == {"PATH": "/usr/bin"}
