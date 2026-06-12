"""Black-box unit tests for :mod:`ralph.pro_support.env`."""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

from ralph.pro_support import env as env_module

if TYPE_CHECKING:
    import pytest


def test_is_pro_mode_false_when_env_empty() -> None:
    """``is_pro_mode`` returns False when ``RALPH_WORKFLOW_PRO`` is unset/empty."""
    assert env_module.is_pro_mode({}) is False
    assert env_module.is_pro_mode({"RALPH_WORKFLOW_PRO": ""}) is False
    assert env_module.is_pro_mode({"RALPH_WORKFLOW_PRO": "   "}) is True


def test_is_pro_mode_true_when_env_set() -> None:
    """``is_pro_mode`` returns True for any non-empty ``RALPH_WORKFLOW_PRO`` value."""
    assert env_module.is_pro_mode({"RALPH_WORKFLOW_PRO": "1"}) is True
    assert env_module.is_pro_mode({"RALPH_WORKFLOW_PRO": "pro"}) is True
    assert env_module.is_pro_mode({"RALPH_WORKFLOW_PRO": "anything"}) is True


def test_is_pro_mode_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no env is supplied, ``is_pro_mode`` reads ``os.environ``."""
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    assert env_module.is_pro_mode() is False
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "1")
    assert env_module.is_pro_mode() is True


def test_get_ralph_workspace_returns_none_when_unset() -> None:
    assert env_module.get_ralph_workspace({}) is None
    assert env_module.get_ralph_workspace({"RALPH_WORKSPACE": ""}) is None


def test_get_ralph_workspace_returns_string_when_set() -> None:
    assert env_module.get_ralph_workspace({"RALPH_WORKSPACE": "/tmp/x"}) == "/tmp/x"


def test_get_ralph_workspace_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RALPH_WORKSPACE", raising=False)
    assert env_module.get_ralph_workspace() is None
    monkeypatch.setenv("RALPH_WORKSPACE", "/tmp/from-os")
    assert env_module.get_ralph_workspace() == "/tmp/from-os"


def test_get_prompt_path_returns_none_when_unset() -> None:
    assert env_module.get_prompt_path({}) is None
    assert env_module.get_prompt_path({"PROMPT_PATH": ""}) is None


def test_get_prompt_path_returns_string_when_set() -> None:
    assert env_module.get_prompt_path({"PROMPT_PATH": "/tmp/p.md"}) == "/tmp/p.md"


def test_get_prompt_path_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPT_PATH", raising=False)
    assert env_module.get_prompt_path() is None
    monkeypatch.setenv("PROMPT_PATH", "/tmp/from-os.md")
    assert env_module.get_prompt_path() == "/tmp/from-os.md"


def test_module_does_not_read_os_environ_at_import() -> None:
    """The module declares the three contract constants; it does NOT read os.environ at import.

    We verify this by removing the env vars and re-importing the
    module — the helpers must then return ``None``/``False``, proving
    no module-level capture happened.
    """
    saved_pro = os.environ.pop("RALPH_WORKFLOW_PRO", None)
    saved_workspace = os.environ.pop("RALPH_WORKSPACE", None)
    saved_prompt = os.environ.pop("PROMPT_PATH", None)
    try:
        importlib.reload(env_module)
        assert env_module.is_pro_mode() is False
        assert env_module.get_ralph_workspace() is None
        assert env_module.get_prompt_path() is None
    finally:
        if saved_pro is not None:
            os.environ["RALPH_WORKFLOW_PRO"] = saved_pro
        if saved_workspace is not None:
            os.environ["RALPH_WORKSPACE"] = saved_workspace
        if saved_prompt is not None:
            os.environ["PROMPT_PATH"] = saved_prompt


def test_three_contract_env_var_constants_are_defined() -> None:
    assert env_module.RALPH_WORKFLOW_PRO == "RALPH_WORKFLOW_PRO"
    assert env_module.RALPH_WORKSPACE == "RALPH_WORKSPACE"
    assert env_module.PROMPT_PATH == "PROMPT_PATH"


def test_os_environ_consulted_when_env_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default behaviour is to read os.environ at call time."""
    monkeypatch.setenv("RALPH_WORKFLOW_PRO", "default-via-os")
    assert env_module.is_pro_mode() is True
    monkeypatch.setenv("RALPH_WORKSPACE", "/default/os/path")
    assert env_module.get_ralph_workspace() == "/default/os/path"
    monkeypatch.setenv("PROMPT_PATH", "/default/os/prompt.md")
    assert env_module.get_prompt_path() == "/default/os/prompt.md"
    # restore to the real os.environ
    monkeypatch.delenv("RALPH_WORKFLOW_PRO", raising=False)
    monkeypatch.delenv("RALPH_WORKSPACE", raising=False)
    monkeypatch.delenv("PROMPT_PATH", raising=False)
    # sanity: helpers are reading os.environ, not a captured snapshot
    assert env_module.is_pro_mode() is bool(os.environ.get("RALPH_WORKFLOW_PRO"))
