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


def _scratch_files(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.iterdir() if path.name.startswith(".") and ".tmp." in path.name
    )


def test_bundled_agents_template_parses_and_activates_claude() -> None:
    with _bundled_agents_template().open("rb") as handle:
        data = tomllib.load(handle)
    assert list(data) == ["agents"], (
        f"the agents template must define only [agents.*], got {list(data)}"
    )
    assert data["agents"]["claude"]["cmd"] == "claude"


def test_bundled_agents_template_carries_every_builtin_block() -> None:
    text = _bundled_agents_template().read_text(encoding="utf-8")
    missing = tuple(
        support.name
        for support in builtin_supports()
        if f"@AGENT-BLOCK-START: {support.name}" not in text
    )
    assert not missing, f"agents template is missing blocks for: {missing!r}"


def test_main_templates_no_longer_ship_agent_definitions() -> None:
    for name in ("ralph-workflow.toml", "ralph-workflow-local.toml"):
        text = (_bundled_dir() / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            body = line.lstrip("#").strip()
            assert not body.startswith("[agents."), (
                f"{name} still ships an agent definition block: {line!r}"
            )


def test_main_template_opens_on_agent_chains() -> None:
    text = (_bundled_dir() / "ralph-workflow.toml").read_text(encoding="utf-8")
    tables = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("[") and line.rstrip().endswith("]")
    ]
    assert tables[0] == "[agent_chains]", f"first table is {tables[0]!r}"


def test_ensure_global_agents_config_skips_a_missing_file(tmp_path: Path) -> None:
    result = ensure_global_agents_config(tmp_path)

    target = tmp_path / AGENTS_FILENAME
    assert result.action == "skipped"
    assert result.path == target
    assert result.backup is None
    assert not target.exists()
    assert not (tmp_path / f"{AGENTS_FILENAME}.bak").exists()
    assert _scratch_files(tmp_path) == []


def test_ensure_global_agents_config_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / AGENTS_FILENAME).write_text('[agents.custom]\ncmd = "mine"\n', encoding="utf-8")
    operator_bytes = (tmp_path / AGENTS_FILENAME).read_bytes()

    result = ensure_global_agents_config(tmp_path)

    assert result.action == "skipped"
    assert "mine" in (tmp_path / AGENTS_FILENAME).read_text()

    assert (tmp_path / AGENTS_FILENAME).read_bytes() == operator_bytes

    assert result.backup is None
    assert not (tmp_path / f"{AGENTS_FILENAME}.bak").exists()
    assert _scratch_files(tmp_path) == []
    assert not (tmp_path / ".git").exists(), (
        "the bootstrap must not create a repository to commit the skipped file into"
    )


def test_regenerate_all_refreshes_the_agents_file(tmp_path: Path) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir()
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (global_dir / AGENTS_FILENAME).write_text('[agents.custom]\ncmd = "mine"\n', encoding="utf-8")
    operator_bytes = (global_dir / AGENTS_FILENAME).read_bytes()

    results = regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    assert (global_dir / f"{AGENTS_FILENAME}.bak").read_text() == (
        '[agents.custom]\ncmd = "mine"\n'
    )
    with (global_dir / AGENTS_FILENAME).open("rb") as handle:
        assert "claude" in tomllib.load(handle)["agents"]

    assert (global_dir / f"{AGENTS_FILENAME}.bak").read_bytes() == operator_bytes

    assert (global_dir / AGENTS_FILENAME).read_text(encoding="utf-8") == (
        _bundled_agents_template().read_text(encoding="utf-8")
    )
    assert "mine" not in (global_dir / AGENTS_FILENAME).read_text(encoding="utf-8")

    agents_results = [r for r in results if r.path.name == AGENTS_FILENAME]
    assert len(agents_results) == 1, (
        f"regenerate_all must report the agents file exactly once; got: {results!r}"
    )
    agents_result = agents_results[0]
    assert agents_result.action == "regenerated"
    assert agents_result.backup == global_dir / f"{AGENTS_FILENAME}.bak"

    assert _scratch_files(global_dir) == []


def test_load_config_reads_agents_from_the_agents_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / AGENTS_FILENAME).write_text('[agents.claude]\ncmd = "from-agents-file"\n')
    (tmp_path / "ralph-workflow.toml").write_text('[agents.claude]\ncmd = "from-main-config"\n')
    local = tmp_path / "local.toml"
    local.write_text("")

    config = load_config(config_path=local)

    assert config.agents["claude"].cmd == "from-main-config"
