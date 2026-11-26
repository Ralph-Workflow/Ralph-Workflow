from __future__ import annotations

from ralph.agents.invoke.agent_search_env import apply_safe_search_env

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_macos_agents_get_a_grep_that_skips_named_pipes": (
        "the agent grep setting now also excludes build and dependency trees, so the test "
        "checks that -D skip still leads the options instead of the exact whole string"
    ),
}


def test_macos_agents_get_a_grep_that_skips_named_pipes() -> None:
    env: dict[str, str] = {}
    apply_safe_search_env(env, platform="darwin")
    options = env["GREP_OPTIONS"].split()
    assert options[:2] == ["-D", "skip"]


def test_macos_agent_recursive_greps_skip_build_and_dependency_trees() -> None:
    env: dict[str, str] = {}
    apply_safe_search_env(env, platform="darwin")
    options = env["GREP_OPTIONS"].split()
    for directory in ("target", "node_modules", ".git"):
        assert f"--exclude-dir={directory}" in options


def test_an_operator_grep_setting_is_kept() -> None:
    env = {"GREP_OPTIONS": "--color=never"}
    apply_safe_search_env(env, platform="darwin")
    assert env["GREP_OPTIONS"] == "--color=never"


def test_linux_agents_are_left_alone_because_gnu_grep_warns_on_the_variable() -> None:
    env: dict[str, str] = {}
    apply_safe_search_env(env, platform="linux")
    assert "GREP_OPTIONS" not in env
