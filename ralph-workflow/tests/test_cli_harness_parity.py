"""Pin that a manual harness run exercises the same code the live pipeline runs.

A harness PASS is only evidence when the harness ran what the pipeline runs.
Two divergences made a PASS worthless:

  1. Each ``smoke-interactive-*`` command pinned a hardcoded
     ``<transport>/<provider>/<model>`` default alias, duplicated as a string
     literal in ``ralph/cli/main.py`` (what the CLI actually used) and in
     ``ralph/cli/commands/smoke.py`` (shadowed, so the two could silently
     disagree). The operator's real pipeline is driven by ``[agent_chains]``
     in their config, so the harness exercised a model the pipeline never
     runs -- and a pinned id goes stale when the provider retires it (the
     Codex harness pinned ``gpt-5-flash`` until it began returning HTTP 400
     and failed for a reason unrelated to Ralph).

  2. Four transports carried a hand-copied trio of ``RALPH_*_BINARY`` override
     helpers. The shared harness applied only AGY and Cursor; the Kimi and
     OpenCode CLI commands applied theirs to a config that the harness then
     discarded, so ``RALPH_OPENCODE_BINARY`` logged "Using ... override" and
     had no effect.

This file is deliberately NOT named after the manual-harness commands it
covers: ``audit_test_policy`` forces any test file whose NAME carries that
word to be marked excluded-from-every-suite, and these regression guards must
run inside ``make verify``.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (the harness plumbing is monkeypatched).
  - No real filesystem outside ``tmp_path``.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
"""

from __future__ import annotations

import inspect
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli import main as cli_main
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands.smoke_agent_defaults import (
    CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS,
    SMOKE_COMMAND_TRANSPORTS,
    resolve_default_smoke_agent,
)
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    smoke_binary_override_env_var,
    smoke_binary_override_transports,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance
from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunResult
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.deps import PipelineDeps


def _cli_agent_defaults() -> dict[str, object]:
    """Return the ``--agent`` Typer default of every ``smoke-*`` CLI command.

    A command that takes no ``--agent`` is absent from the mapping; one that
    takes it maps to the Typer option's ``default`` value.
    """
    defaults: dict[str, object] = {}
    for command in cli_main.app.registered_commands:
        name = command.name
        callback: Callable[..., None] | None = command.callback
        if name is None or callback is None or not name.startswith("smoke-"):
            continue
        parameter = inspect.signature(callback).parameters.get("agent")
        if parameter is None:
            continue
        option: object = parameter.default
        defaults[name] = getattr(option, "default", option)
    return defaults


# ---------------------------------------------------------------------------
# Divergence 1 -- no harness default may pin a provider/model literal.
# ---------------------------------------------------------------------------


@pytest.mark.timeout_seconds(3)
def test_every_agent_taking_command_defers_its_default_to_the_config() -> None:
    """No ``--agent`` default is a hardcoded alias; each defers to the config."""
    offenders: list[str] = []
    for name, default in _cli_agent_defaults().items():
        if name in CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS:
            continue
        if name not in SMOKE_COMMAND_TRANSPORTS:
            offenders.append(f"{name}: not declared in SMOKE_COMMAND_TRANSPORTS")
            continue
        if default is not None:
            offenders.append(f"{name}: CLI default {default!r}")
    assert offenders == []


@pytest.mark.timeout_seconds(3)
def test_command_seam_default_matches_the_cli_default() -> None:
    """The command-function default is not a second, shadowed source of truth."""
    offenders: list[str] = []
    for name, transport in SMOKE_COMMAND_TRANSPORTS.items():
        command = getattr(smoke_module, f"smoke_interactive_{transport.value}_command")
        default = inspect.signature(command).parameters["agent_name"].default
        if default is not None:
            offenders.append(f"{name}: command default {default!r}")
    assert offenders == []


@pytest.mark.timeout_seconds(3)
def test_default_resolves_to_the_first_configured_chain_alias() -> None:
    """The default is the operator's own chain entry for that transport."""
    config = UnifiedConfig(
        agent_chains={
            "planning": ["cursor/auto", "pi/omnirouter/kmc/k3"],
            "development": ["codex/gpt-5.6-terra", "pi/omnirouter/cx/gpt-5.6-terra-medium"],
        }
    )
    lookup = AgentRegistry.from_config(config).get

    assert resolve_default_smoke_agent(AgentTransport.PI, config, lookup) == (
        "pi/omnirouter/kmc/k3"
    )
    assert resolve_default_smoke_agent(AgentTransport.CODEX, config, lookup) == (
        "codex/gpt-5.6-terra"
    )


@pytest.mark.timeout_seconds(3)
def test_default_falls_back_to_the_bare_transport_alias() -> None:
    """A transport the operator's chains never name uses the bare alias."""
    config = UnifiedConfig(agent_chains={"development": ["claude/sonnet"]})
    lookup = AgentRegistry.from_config(config).get

    assert resolve_default_smoke_agent(AgentTransport.OPENCODE, config, lookup) == "opencode"
    assert resolve_default_smoke_agent(AgentTransport.KIMI, config, lookup) == "kimi"


@pytest.mark.timeout_seconds(3)
def test_command_without_agent_flag_runs_the_configured_chain_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``smoke-interactive-cursor`` with no ``--agent`` runs the operator's alias."""
    config = UnifiedConfig(
        agent_chains={"development": ["cursor/gpt-5.3-codex-high", "claude/sonnet"]}
    )
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agent")
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: config)
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_cursor_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "cursor/gpt-5.3-codex-high"


@pytest.mark.timeout_seconds(3)
def test_explicit_agent_flag_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator-supplied ``--agent`` overrides the configured default."""
    config = UnifiedConfig(agent_chains={"development": ["cursor/auto"]})
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agent")
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: config)
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_cursor_command(
        agent_name="cursor/gpt-5.3-codex-high",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "cursor/gpt-5.3-codex-high"


# ---------------------------------------------------------------------------
# Divergence 2 -- every override transport is applied in the shared harness.
# ---------------------------------------------------------------------------


def _executable_stub(tmp_path: Path, name: str) -> Path:
    """Write an executable no-op stub binary under ``tmp_path``."""
    stub = tmp_path / name
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


class _FakePipelineFactory:
    """Stand-in composition root so the harness never builds real deps."""

    def build(self, *_args: object, **_kwargs: object) -> PipelineDeps | None:
        """Return ``None``; the fake plumbing never reads the deps bundle."""
        return None


@pytest.mark.timeout_seconds(3)
def test_override_table_covers_every_documented_env_var() -> None:
    """The table is the single source of truth for the four override vars."""
    assert {
        smoke_binary_override_env_var(transport)
        for transport in smoke_binary_override_transports()
    } == {
        "RALPH_AGY_BINARY",
        "RALPH_CURSOR_BINARY",
        "RALPH_KIMI_BINARY",
        "RALPH_OPENCODE_BINARY",
    }


@pytest.mark.timeout_seconds(3)
@pytest.mark.parametrize("transport", list(smoke_binary_override_transports()))
def test_shared_harness_applies_every_override_transport(
    transport: AgentTransport,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The harness rewrites the agent's ``cmd`` for EVERY override transport."""
    stub = _executable_stub(tmp_path, f"stub-{transport.value}")
    env_var = smoke_binary_override_env_var(transport)
    assert env_var is not None
    monkeypatch.setenv(env_var, str(stub))
    monkeypatch.setenv("RALPH_BROKER_SECRET", "0" * 64)
    agent_name = transport.value
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.setattr(smoke_module, "DefaultPipelineFactory", _FakePipelineFactory)
    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> SmokeRunResult:
        captured["config"] = kwargs["config"]
        return SmokeRunResult(
            agent_name=agent_name,
            transport=transport.value,
            output_file=tmp_path / "todo-list.js",
            file_created=True,
            session_id="sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    smoke_module.smoke_harness_agent_command(agent_name, display_context=None)

    effective = captured["config"]
    assert isinstance(effective, UnifiedConfig)
    applied = effective.agents.get(agent_name)
    assert applied is not None, f"{transport.value}: harness discarded the override"
    assert applied.cmd == shlex.quote(str(stub))


@pytest.mark.timeout_seconds(3)
def test_override_is_a_no_op_for_a_transport_without_one() -> None:
    """A transport with no override entry is returned unchanged."""
    agent_config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)

    assert apply_smoke_binary_override(agent_config) is agent_config
