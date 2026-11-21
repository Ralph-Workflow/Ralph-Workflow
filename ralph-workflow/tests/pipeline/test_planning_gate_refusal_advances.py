from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import resolve_phase_required_artifact, retry_hint_path
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.publication_gate import PublicationGateRun, check_publication_gate
from ralph.policy.loader import cached_default_policy_bundle
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig

pytestmark = [pytest.mark.timeout_seconds(20)]

_GATE_OUTPUT = "[verify] FAILED: ruff check ralph/ tests/\n"
_POLICY = Path("docs") / "ralph-workflow-policy" / "verification-policy.md"
_PLAN = (
    "---\n"
    "type: plan\n"
    "---\n"
    "# Plan\n"
    "1. This is a complete test plan with more than ten words in order to satisfy the validator.\n"
)


class _Registry:
    def get(self, name: str) -> AgentConfig:
        from ralph.config.models import AgentConfig

        return AgentConfig(cmd=name)


class _StopRun(KeyboardInterrupt):
    pass


def _red_gate(_command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
    return PublicationGateRun(exit_code=1, output=_GATE_OUTPUT, timed_out=False)


def _feature_ahead_with_a_refused_tree(root: Path) -> None:
    repo = Repo(root)
    try:
        repo.create_head("feature").checkout()
        workspace = FsWorkspace(root)
        workspace.write(
            _POLICY.as_posix(),
            "# Verification\n\nRALPH-FACT: authoritative_verify_command: `make verify`\n",
        )
        workspace.write("PROMPT.md", "Implement the change.\n")
        repo.index.add([_POLICY.as_posix(), "PROMPT.md"])
        repo.index.commit("run commit the gate refuses")
    finally:
        repo.close()
    refused = check_publication_gate(root, "HEAD", runner=_red_gate)
    assert refused is not None
    assert refused.block is not None


def _invoked_phase(prompt: Path) -> str:
    return prompt.name.removesuffix(".md").removesuffix("_prompt")


def test_a_gate_refused_tree_lets_a_successful_planning_pass_advance(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    _feature_ahead_with_a_refused_tree(root)
    bundle = cached_default_policy_bundle()
    runner_module.register_role_handlers(bundle.pipeline)
    invoked: list[str] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del config, options, _clock
        prompt = Path(prompt_file)
        if prompt.name == CANARY_PROMPT_FILENAME:
            return iter(["ok\n"])
        invoked.append(_invoked_phase(prompt))
        if len(invoked) > 1:
            raise _StopRun
        plan = root / ".agent" / "artifacts" / "plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(_PLAN, encoding="utf-8")
        return iter(["planned\n"])

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _Registry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
        invoke_agent=fake_invoke_agent,
    )
    deps = replace(
        deps,
        phase_prompt_materializer=materialize_prompt_for_phase,
        auto_integrate_resolver=None,
    )
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
                "max_same_agent_retries": 0,
            }
        }
    )
    with (
        patch("ralph.pipeline.runner.resolve_workspace_scope", return_value=WorkspaceScope(root)),
        patch("ralph.pipeline.runner.write_start_commit_if_absent"),
        patch("ralph.pipeline.runner.validate_custom_mcp_servers", return_value=0),
    ):
        run_loop.run(config, pipeline_deps=deps, verbosity=Verbosity.QUIET)

    assert invoked == ["planning", "planning_analysis"]
    planning_hint = root / retry_hint_path("planning", pipeline_policy=bundle.pipeline)
    assert not planning_hint.exists()
    development_hint = root / retry_hint_path("development", pipeline_policy=bundle.pipeline)
    assert _GATE_OUTPUT.strip() in development_hint.read_text(encoding="utf-8")
