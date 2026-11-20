from __future__ import annotations

import os
import subprocess
from importlib import import_module
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.git.hardening import GIT_DIR_ENV_KEYS
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller, run_agent_attempt
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_AGENT = "fake"

import_module("ralph.pipeline.runner")


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in GIT_DIR_ENV_KEYS}


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env if env is not None else _clean_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _config() -> UnifiedConfig:
    return UnifiedConfig(
        agents={_AGENT: AgentConfig(cmd="/opt/bin/fake-agent", json_parser="generic")}
    )


def _shared_core(main: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", "--file", str(main / ".git" / "config"), "--get", key],
        env=_clean_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() or None


@pytest.mark.parametrize(
    ("command", "key", "expected"),
    [
        (("config", "core.bare", "true"), "core.bare", "false"),
        (("config", "core.worktree", "/elsewhere"), "core.worktree", None),
    ],
)
def test_an_agent_attempt_that_breaks_the_shared_git_config_is_repaired(
    tmp_path: Path, command: tuple[str, ...], key: str, expected: str | None
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(
        main,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    linked_git_dir = _git(linked, "rev-parse", "--absolute-git-dir")

    def bisecting_agent(*_args: object, **_kwargs: object) -> object:
        _git(tmp_path, *command, env={**_clean_env(), "GIT_DIR": linked_git_dir})
        assert _shared_core(main, key) != expected
        return PipelineEvent.AGENT_SUCCESS

    deps = AttemptRunnerDeps(SystemClock(), bisecting_agent, None, None, None)
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP, config=_config(), policy_bundle=None, deps=deps
    )
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=InvokeAgentEffect(agent_name=_AGENT, phase="development", prompt_file="p.md"),
            config=_config(),
            controller=controller,
            deps=deps,
            workspace_scope=WorkspaceScope(root=linked),
        )
    finally:
        logger.remove(sink)

    assert _shared_core(main, key) == expected
    assert _git(linked, "rev-parse", "--is-inside-work-tree") == "true"
    repairs = [line for line in warnings if "shared git config" in line]
    assert len(repairs) == 1
    assert key in repairs[0]
    assert "development" in repairs[0]
