from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.process import child_env_for_spawn

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_spawn_env_regression_pwd_names_the_child_working_directory() -> None:
    child = child_env_for_spawn({"PWD": "/somewhere/else", "HOME": "/home/x"}, cwd="/work/repo")

    assert child is not None
    assert child["PWD"] == "/work/repo"
    assert child["HOME"] == "/home/x"


def test_spawn_env_regression_oldpwd_is_dropped_when_pwd_is_realigned() -> None:
    child = child_env_for_spawn({"PWD": "/a", "OLDPWD": "/b"}, cwd="/work/repo")

    assert child is not None
    assert "OLDPWD" not in child


def test_spawn_env_leaves_pwd_alone_when_the_child_inherits_the_directory() -> None:
    child = child_env_for_spawn({"PWD": "/a"}, cwd=None)

    assert child is not None
    assert child["PWD"] == "/a"


def test_spawn_env_scrubs_private_controls_when_caller_requests_inheritance(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_BROKER_SECRET", "parent-only")
    monkeypatch.setenv("RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL", "parent-only")
    monkeypatch.setenv("SPAWN_ENV_PUBLIC", "visible")

    child = child_env_for_spawn(None)

    assert child is not None
    assert child["SPAWN_ENV_PUBLIC"] == "visible"
    assert "RALPH_BROKER_SECRET" not in child
    assert "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL" not in child
    assert os.environ["RALPH_BROKER_SECRET"] == "parent-only"


def test_relay_handoff_does_not_implicitly_authorize_broker_secret() -> None:
    parent = {
        "RALPH_BROKER_SECRET": "parent-only",
        "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "relay",
    }

    child = child_env_for_spawn(parent, allow_activity_relay_controls=True)

    assert "RALPH_BROKER_SECRET" not in child
    assert child["RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL"] == "relay"


def test_mcp_bootstrap_requires_explicit_broker_secret_handoff() -> None:
    parent = {"RALPH_BROKER_SECRET": "parent-only"}

    child = child_env_for_spawn(parent, allow_broker_secret=True)

    assert child["RALPH_BROKER_SECRET"] == "parent-only"
