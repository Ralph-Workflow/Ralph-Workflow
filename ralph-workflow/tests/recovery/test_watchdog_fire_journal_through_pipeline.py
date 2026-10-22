"""Every watchdog kill, including the one that ends the run, is journaled.

Drives the real ``execute_agent_effect`` retry loop with an agent that is
killed by the watchdog on every attempt. The kill that exhausts the retry
budget is the one an operator investigates, so it must reach
``.agent/watchdog-fire-events.jsonl`` just like the retried ones.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    InvokeOptions,
)
from ralph.checked_accessors import as_mapping
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.scope import WorkspaceScope
from tests._phases_retry_on_stale_session_helper__fakeregistryinstance import (
    _FakeRegistryInstance,
)
from tests._pipeline_deps_factory import make_test_pipeline_deps

_EVENTS = Path(".agent") / "watchdog-fire-events.jsonl"
_MAX_RETRIES = 2


class _Bridge:
    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:19999/mcp"

    def reset_tool_registry(self) -> None:
        return


class _Registry:
    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _FakeRegistryInstance:
        del cls, config
        return _FakeRegistryInstance(AgentConfig(cmd="claude", output_flag="--json-stream"))


def _run_always_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, resolver: bool = False
) -> list[str]:
    """Run the retry loop with a watchdog kill on every attempt; return attempt prompts."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent" / "tmp").mkdir(parents=True)
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("implement the change", encoding="utf-8")
    prompts: list[str] = []

    def fake_invoke_agent(
        config: AgentConfig, prompt_file: str, *, options: InvokeOptions | None = None
    ) -> list[str]:
        del config, options
        prompts.append(prompt_file)
        raise AgentInactivityTimeoutError(
            "claude",
            300.0,
            [f"distinct output {len(prompts)}"],
            InactivityTimeoutOpts(
                reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                diagnostic={"watchdog_snapshot": {"mcp_tool_call_count": len(prompts)}},
            ),
        )

    ctx = make_display_context()
    deps = make_test_pipeline_deps(
        ctx,
        bridge=_Bridge(),
        registry_factory=_Registry.from_config,
        master_prompt_materializer=lambda *_a, **_k: str(tmp_path / "SYS.md"),
    )
    config = UnifiedConfig(general=GeneralConfig(max_same_agent_retries=_MAX_RETRIES))
    with contextlib.suppress(AgentInactivityTimeoutError):
        effect_executor_module.execute_agent_effect(
            InvokeAgentEffect(
                agent_name="claude",
                phase="development",
                prompt_file=str(prompt),
                activity_only_supervision=resolver,
            ),
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=ctx,
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
        )
    return prompts


def test_each_kill_including_the_final_one_has_a_journal_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _run_always_killed(tmp_path, monkeypatch)

    lines = (tmp_path / _EVENTS).read_text(encoding="utf-8").splitlines()
    seen = [
        as_mapping(as_mapping(json.loads(line))["snapshot"])["mcp_tool_call_count"]
        for line in lines
    ]
    assert len(prompts) > 1
    assert seen == list(range(1, len(prompts) + 1))


def test_a_conflict_resolver_kill_with_no_retry_budget_is_still_journaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolver gets zero executor retries, so its kill never reaches a retry plan."""
    prompts = _run_always_killed(tmp_path, monkeypatch, resolver=True)

    lines = (tmp_path / _EVENTS).read_text(encoding="utf-8").splitlines()
    assert len(prompts) == 1
    assert len(lines) == 1
