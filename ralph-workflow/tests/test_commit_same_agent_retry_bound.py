from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ralph.cli.commands.commit as commit_cli
from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.plumbing import commit_plumbing
from ralph.policy.models import AgentsPolicy
from ralph.timeout_defaults import AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.monkeypatch import MonkeyPatch

    from ralph.config.enums import AgentTransport

_STUCK_AGENT = "stuck-agent"
_NEXT_AGENT = "next-agent"
_SAME_AGENT_RETRIES = 10


def _write_master_prompt(
    workspace_root: Path,
    name: str,
    default_product_criteria: str | None = None,
    worker_namespace: Path | None = None,
    planning_style: bool = False,
    transport: AgentTransport | None = None,
) -> str:
    del default_product_criteria, planning_style, transport
    path = Path(worker_namespace or workspace_root) / ".agent" / "tmp" / f"master_prompt_{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("master prompt", encoding="utf-8")
    return str(path)


def _run_commit_chain(tmp_path: Path, monkeypatch: MonkeyPatch) -> list[str]:
    registry = AgentRegistry.from_config(
        UnifiedConfig(
            agents={
                name: AgentConfig(cmd=name, output_flag="--json")
                for name in (_STUCK_AGENT, _NEXT_AGENT)
            }
        )
    )
    invocations: list[str] = []

    def exits_without_completion(
        agent: AgentConfig, *_args: object, **_kwargs: object
    ) -> Iterator[str]:
        agent_name = agent.cmd
        invocations.append(agent_name)

        def _output() -> Iterator[str]:
            yield f"Session ID: {agent_name}-session"
            raise OpenCodeResumableExitError(agent_name, f"{agent_name}-session")

        return _output()

    display_context = make_display_context()
    monkeypatch.setattr(commit_cli, "invoke_agent", exits_without_completion)
    commit_plumbing.run_commit_plumbing(
        diff="diff --git a/file.txt b/file.txt",
        repo_root=tmp_path,
        chain_config=commit_cli.CommitChainConfig(
            registry=registry,
            agents=[_STUCK_AGENT, _NEXT_AGENT],
            verbose=False,
            agents_policy=AgentsPolicy(),
            general_config=UnifiedConfig(
                general=GeneralConfig(max_same_agent_retries=_SAME_AGENT_RETRIES)
            ),
        ),
        display_context=display_context,
        pipeline_deps=make_test_pipeline_deps(
            display_context,
            master_prompt_materializer=_write_master_prompt,
            registry_factory=lambda _config: registry,
            recovery_clock=FakeClock(start=0.0),
        ),
    )
    return invocations


def test_a_commit_agent_that_never_completes_stops_at_the_unproductive_limit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    invocations = _run_commit_chain(tmp_path, monkeypatch)

    assert invocations.count(_STUCK_AGENT) == AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT


def test_a_commit_agent_that_never_completes_falls_over_to_the_next_chain_agent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    invocations = _run_commit_chain(tmp_path, monkeypatch)

    assert _NEXT_AGENT in invocations
