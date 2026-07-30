"""Regression tests for the subprocess-env anti-forgery boundary.

RFC-013 P3 binds completion sentinels and receipt HMACs to a
broker-owned secret (``RALPH_BROKER_SECRET``). The orchestrator
(parent) process reads the secret to verify sentinels and receipts,
but the secret MUST NOT be inherited by spawned agent subprocesses
— otherwise an agent with workspace-write access could forge
completion claims.

The relevant seam is ``_subprocess_env`` in
``ralph/agents/invoke/_process_reader.py``. Both the regular
subprocess path (``_process_reader.py:864``) and the PTY path
(``_pty_runner.py:67``) call this helper. If the helper inherits
``RALPH_BROKER_SECRET`` via ``os.environ.copy()``, the boundary is
broken end-to-end.

These tests pin the contract that:

1. ``_subprocess_env`` never carries ``RALPH_BROKER_SECRET`` even
   when the parent process has it set.
2. ``_subprocess_env`` honours caller-supplied ``extra_env`` but
   cannot override the strip with a caller-supplied
   ``RALPH_BROKER_SECRET``.
3. The strip is consistent regardless of whether ``extra_env`` is
   ``None``, empty, or pre-populated with unrelated keys.
"""

from __future__ import annotations

import os

import pytest

from ralph.agents.invoke._process_reader import _subprocess_env

_BROKER_SECRET_KEY = "RALPH_BROKER_SECRET"
_BROKER_SECRET_VALUE = "super-secret-broker-hmac-key"


@pytest.fixture
def broker_secret_in_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the broker-owned HMAC secret present in the parent."""
    monkeypatch.setenv(_BROKER_SECRET_KEY, _BROKER_SECRET_VALUE)


def test_subprocess_env_strips_broker_secret(broker_secret_in_parent: object) -> None:
    """A plain call (no extra_env) must NOT include RALPH_BROKER_SECRET."""
    env = _subprocess_env(None)
    assert _BROKER_SECRET_KEY not in env, (
        f"_subprocess_env leaked {_BROKER_SECRET_KEY!r} to child env: "
        f"{env.get(_BROKER_SECRET_KEY)!r}"
    )


def test_subprocess_env_strips_broker_secret_with_empty_extra_env(
    broker_secret_in_parent: object,
) -> None:
    """An empty ``extra_env`` dict still must NOT include the broker secret."""
    env = _subprocess_env({})
    assert _BROKER_SECRET_KEY not in env


def test_subprocess_env_strips_broker_secret_with_unrelated_extra_env(
    broker_secret_in_parent: object,
) -> None:
    """Unrelated ``extra_env`` keys are preserved; the broker secret is still stripped."""
    env = _subprocess_env({"FOO": "bar", "BAZ": "qux"})
    assert _BROKER_SECRET_KEY not in env
    assert env.get("FOO") == "bar"
    assert env.get("BAZ") == "qux"


def test_subprocess_env_caller_cannot_override_strip(
    broker_secret_in_parent: object,
) -> None:
    """A caller-supplied ``extra_env`` value for ``RALPH_BROKER_SECRET``
    MUST NOT survive the strip — the parent-side broker decision is
    authoritative. This prevents a caller from smuggling the secret
    into the child through ``extra_env``."""
    env = _subprocess_env({_BROKER_SECRET_KEY: "attacker-supplied-value"})
    assert _BROKER_SECRET_KEY not in env, (
        "_subprocess_env honoured an extra_env override of RALPH_BROKER_SECRET; "
        "the parent-side broker decision must always win."
    )


def test_subprocess_env_does_not_mutate_parent_environ(
    broker_secret_in_parent: object,
) -> None:
    """The strip must not mutate ``os.environ`` itself — only the
    returned dict is sanitised."""
    env = _subprocess_env(None)
    assert _BROKER_SECRET_KEY not in env
    assert os.environ.get(_BROKER_SECRET_KEY) == _BROKER_SECRET_VALUE, (
        "_subprocess_env leaked the strip back into os.environ"
    )
