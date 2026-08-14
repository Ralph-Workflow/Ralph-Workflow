"""Pin the CCS smoke command surface and harness-spec resolution.

Before this change, ``resolve_smoke_harness_spec`` had no branch for
``ccs/<alias>`` and raised ``ValueError: No smoke harness spec defined
for agent 'ccs/glm'`` for every CCS alias, and there was no
``ralph smoke-interactive-ccs`` command at all (unlike every other
supported agent transport -- agy, codex, cursor, nanocoder, opencode,
pi all have a dedicated ``smoke-interactive-*`` command). ``ccs/glm``
could not be smoke tested through the ``ralph`` CLI in any way.

The tests below pin:

  1. ``resolve_smoke_harness_spec("ccs/glm")`` resolves to a
     sanitized, alias-scoped directory (mirrors the ``codex/<model>``
     and ``pi/<model>`` layout) instead of raising.
  2. Two different CCS aliases (``ccs/glm`` vs ``ccs/work``) resolve
     to different directories so concurrent smoke runs do not
     collide on completion-sentinel / receipt paths.
  3. ``ccs/`` with an empty alias still raises (no smoke harness
     spec defined) rather than silently degrading to a shared path.
  4. ``smoke_interactive_ccs_command`` exits 2 when the ``ccs``
     binary is missing.
  5. ``smoke_interactive_ccs_command`` exits 2 when the agent name
     does not resolve to a CCS command.
  6. ``smoke_interactive_ccs_command`` delegates to the shared
     harness when the binary is present and the alias resolves.
  7. ``ralph smoke-interactive-ccs --help`` advertises ``--agent``,
     ``--subagents``, and ``--subagent-prompt-file``.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (the harness plumbing is monkeypatched).
  - No real filesystem outside ``CliRunner``'s in-memory virtual FS.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralph.agents import registry as registry_module
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.main import app
from ralph.config import loader as loader_module
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec
from ralph.workspace import scope as scope_module
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.smoke

_RUNNER = CliRunner()


def test_resolve_smoke_harness_spec_ccs_glm() -> None:
    """AC #1: ``ccs/glm`` resolves to a sanitized, alias-scoped layout."""
    spec = resolve_smoke_harness_spec("ccs/glm")

    assert spec.agent_name == "ccs/glm"
    assert spec.relative_dir.as_posix() == "tmp/interactive-ccs-smoke/glm"
    assert spec.output_file.as_posix() == "tmp/interactive-ccs-smoke/glm/todo-list.js"
    assert spec.run_id == "interactive-ccs-smoke-glm"


def test_resolve_smoke_harness_spec_ccs_aliases_do_not_collide() -> None:
    """AC #2: two CCS aliases resolve to distinct directories and run ids."""
    glm_spec = resolve_smoke_harness_spec("ccs/glm")
    work_spec = resolve_smoke_harness_spec("ccs/work")

    assert glm_spec.relative_dir != work_spec.relative_dir
    assert glm_spec.output_file != work_spec.output_file
    assert glm_spec.run_id != work_spec.run_id


def test_resolve_smoke_harness_spec_ccs_empty_alias_raises() -> None:
    """AC #3: ``ccs/`` with no alias still raises rather than degrading silently."""
    with pytest.raises(ValueError, match="No smoke harness spec defined"):
        resolve_smoke_harness_spec("ccs/")


def test_smoke_interactive_ccs_command_exits_when_ccs_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #4: missing ``ccs`` binary is a hard exit(2), not a crash."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    exit_code = smoke_module.smoke_interactive_ccs_command(display_context=None)

    assert exit_code == 2


def test_smoke_interactive_ccs_command_exits_when_agent_resolves_to_wrong_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC #5: an alias that does not resolve to a ``ccs``-cmd is rejected."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")

    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, _name: str) -> AgentConfig | None:
            return AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)

    exit_code = smoke_module.smoke_interactive_ccs_command(
        agent_name="claude/haiku",
        display_context=None,
    )

    assert exit_code == 2


def test_smoke_interactive_ccs_command_runs_harness_when_binary_present_and_alias_resolves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC #6: a resolvable ``ccs/<alias>`` delegates to the shared harness."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")

    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "ccs/glm":
                return AgentConfig(
                    cmd="ccs glm",
                    transport=AgentTransport.CLAUDE,
                )
            return None

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **kwargs: object) -> int:
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_ccs_command(
        agent_name="ccs/glm",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "ccs/glm"


def test_cli_help_advertises_ccs_smoke_options() -> None:
    """AC #7: ``ralph smoke-interactive-ccs --help`` advertises the CCS option surface."""
    result = _RUNNER.invoke(app, ["smoke-interactive-ccs", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "--agent" in output
    assert "--subagents" in output
    assert "--subagent-prompt-file" in output
