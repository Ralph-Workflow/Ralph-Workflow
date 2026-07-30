"""Behavior tests for the dedicated agent-CLI-definition config file.

``[agents.*]`` entries are transport plumbing -- binary name, flags, output
parser -- not an operator decision. They used to sit in the middle of the main
``ralph-workflow.toml`` template, ahead of the one section operators actually
edit (``[agent_chains]``). They now live in their own file,
``ralph-workflow-agents.toml``, so the main config opens on the chains.

The tests pin the operator-visible contract:

* the bundled agents template exists, parses, and carries every built-in
  agent block;
* neither main-config template still ships an ``[agents.*]`` block;
* the new file is created on first run and refreshed by regeneration;
* ``load_config`` reads ``[agents.*]`` from it; and
* an ``[agents.*]`` table left behind in an existing ``ralph-workflow.toml``
  still wins, so upgrading installs keep working.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.builtin import builtin_supports
from ralph.config import bootstrap as _config_bootstrap
from ralph.config.bootstrap import ensure_global_agents_config, regenerate_all
from ralph.config.loader import load_config

if TYPE_CHECKING:
    import pytest

AGENTS_FILENAME = "ralph-workflow-agents.toml"


def _bundled_dir() -> Path:
    return _config_bootstrap._get_bundled_defaults_dir()


def _bundled_agents_template() -> Path:
    return _bundled_dir() / AGENTS_FILENAME


def test_bundled_agents_template_parses_and_activates_claude() -> None:
    """The bundled agents template is valid TOML with claude active."""
    with _bundled_agents_template().open("rb") as handle:
        data = tomllib.load(handle)
    assert list(data) == ["agents"], (
        f"the agents template must define only [agents.*], got {list(data)}"
    )
    assert data["agents"]["claude"]["cmd"] == "claude"


def test_bundled_agents_template_carries_every_builtin_block() -> None:
    """Every built-in agent has an uncommentable block in the agents template."""
    text = _bundled_agents_template().read_text(encoding="utf-8")
    missing = tuple(
        support.name
        for support in builtin_supports()
        if f"@AGENT-BLOCK-START: {support.name}" not in text
    )
    assert not missing, f"agents template is missing blocks for: {missing!r}"


def test_main_templates_no_longer_ship_agent_definitions() -> None:
    """Neither main-config template still carries [agents.*] plumbing.

    A regression that reintroduces an agent block here puts transport
    plumbing back ahead of [agent_chains], which is the section operators
    actually edit.
    """
    for name in ("ralph-workflow.toml", "ralph-workflow-local.toml"):
        text = (_bundled_dir() / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            body = line.lstrip("#").strip()
            assert not body.startswith("[agents."), (
                f"{name} still ships an agent definition block: {line!r}"
            )


def test_main_template_opens_on_agent_chains() -> None:
    """[agent_chains] is the first table in the main template."""
    text = (_bundled_dir() / "ralph-workflow.toml").read_text(encoding="utf-8")
    tables = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("[") and line.rstrip().endswith("]")
    ]
    assert tables[0] == "[agent_chains]", f"first table is {tables[0]!r}"


def test_ensure_global_agents_config_creates_the_file(tmp_path: Path) -> None:
    """First run seeds ~/.config/ralph-workflow-agents.toml."""
    result = ensure_global_agents_config(tmp_path)

    target = tmp_path / AGENTS_FILENAME
    assert result.action == "created"
    assert target.is_file()
    with target.open("rb") as handle:
        assert "claude" in tomllib.load(handle)["agents"]


def test_ensure_global_agents_config_is_idempotent(tmp_path: Path) -> None:
    """A second run leaves an existing file untouched."""
    ensure_global_agents_config(tmp_path)
    (tmp_path / AGENTS_FILENAME).write_text('[agents.custom]\ncmd = "mine"\n')

    result = ensure_global_agents_config(tmp_path)

    assert result.action == "skipped"
    assert "mine" in (tmp_path / AGENTS_FILENAME).read_text()


def test_regenerate_all_refreshes_the_agents_file(tmp_path: Path) -> None:
    """--regenerate-config rewrites the agents file and backs up the old one."""
    global_dir = tmp_path / "config"
    global_dir.mkdir()
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (global_dir / AGENTS_FILENAME).write_text('[agents.custom]\ncmd = "mine"\n')

    regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    assert (global_dir / f"{AGENTS_FILENAME}.bak").read_text() == (
        '[agents.custom]\ncmd = "mine"\n'
    )
    with (global_dir / AGENTS_FILENAME).open("rb") as handle:
        assert "claude" in tomllib.load(handle)["agents"]


def test_load_config_reads_agents_from_the_agents_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """[agents.*] defined in the agents file reaches the merged config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / AGENTS_FILENAME).write_text(
        '[agents.mytool]\ncmd = "mytool"\ndisplay_name = "My Tool"\n'
    )
    local = tmp_path / "local.toml"
    local.write_text("")

    config = load_config(config_path=local)

    assert config.agents["mytool"].cmd == "mytool"


def test_agents_in_legacy_main_config_still_win(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An [agents.*] table left in ralph-workflow.toml keeps its precedence.

    Operators upgrading from a release that templated agents into the main
    config must not silently lose their customized block.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / AGENTS_FILENAME).write_text('[agents.claude]\ncmd = "from-agents-file"\n')
    (tmp_path / "ralph-workflow.toml").write_text('[agents.claude]\ncmd = "from-main-config"\n')
    local = tmp_path / "local.toml"
    local.write_text("")

    config = load_config(config_path=local)

    assert config.agents["claude"].cmd == "from-main-config"
