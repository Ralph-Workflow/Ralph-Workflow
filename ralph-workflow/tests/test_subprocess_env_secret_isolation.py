from __future__ import annotations

import os

import pytest

from ralph.agents.invoke._process_reader import _subprocess_env

_BROKER_SECRET_KEY = "RALPH_BROKER_SECRET"
_BROKER_SECRET_VALUE = "super-secret-broker-hmac-key"


@pytest.fixture
def broker_secret_in_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_BROKER_SECRET_KEY, _BROKER_SECRET_VALUE)


def test_subprocess_env_strips_broker_secret(broker_secret_in_parent: object) -> None:
    env = _subprocess_env(None)
    assert _BROKER_SECRET_KEY not in env, (
        f"_subprocess_env leaked {_BROKER_SECRET_KEY!r} to child env: "
        f"{env.get(_BROKER_SECRET_KEY)!r}"
    )


def test_subprocess_env_strips_broker_secret_with_empty_extra_env(
    broker_secret_in_parent: object,
) -> None:
    env = _subprocess_env({})
    assert _BROKER_SECRET_KEY not in env


def test_subprocess_env_strips_broker_secret_with_unrelated_extra_env(
    broker_secret_in_parent: object,
) -> None:
    env = _subprocess_env({"FOO": "bar", "BAZ": "qux"})
    assert _BROKER_SECRET_KEY not in env
    assert env.get("FOO") == "bar"
    assert env.get("BAZ") == "qux"


def test_subprocess_env_caller_cannot_override_strip(
    broker_secret_in_parent: object,
) -> None:
    env = _subprocess_env({_BROKER_SECRET_KEY: "attacker-supplied-value"})
    assert _BROKER_SECRET_KEY not in env, (
        "_subprocess_env honoured an extra_env override of RALPH_BROKER_SECRET; "
        "the parent-side broker decision must always win."
    )


def test_subprocess_env_does_not_mutate_parent_environ(
    broker_secret_in_parent: object,
) -> None:
    env = _subprocess_env(None)
    assert _BROKER_SECRET_KEY not in env
    assert os.environ.get(_BROKER_SECRET_KEY) == _BROKER_SECRET_VALUE, (
        "_subprocess_env leaked the strip back into os.environ"
    )


def test_cursor_subprocess_env_strips_inherited_ssh_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markers = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")
    for marker in markers:
        monkeypatch.setenv(marker, "hostile")

    env = _subprocess_env({"AGENT_CLI_CREDENTIAL_STORE": "file"})

    assert not set(markers) & env.keys()
