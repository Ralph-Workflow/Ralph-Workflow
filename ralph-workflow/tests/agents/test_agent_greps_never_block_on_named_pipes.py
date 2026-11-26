from __future__ import annotations

from ralph.agents.invoke.agent_search_env import apply_safe_search_env


def test_macos_agents_get_a_grep_that_skips_named_pipes() -> None:
    env: dict[str, str] = {}
    apply_safe_search_env(env, platform="darwin")
    assert env["GREP_OPTIONS"] == "-D skip"


def test_an_operator_grep_setting_is_kept() -> None:
    env = {"GREP_OPTIONS": "--color=never"}
    apply_safe_search_env(env, platform="darwin")
    assert env["GREP_OPTIONS"] == "--color=never"


def test_linux_agents_are_left_alone_because_gnu_grep_warns_on_the_variable() -> None:
    env: dict[str, str] = {}
    apply_safe_search_env(env, platform="linux")
    assert "GREP_OPTIONS" not in env
