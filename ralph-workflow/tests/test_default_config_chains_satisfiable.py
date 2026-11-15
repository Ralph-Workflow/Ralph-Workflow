from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from ralph.agents.catalog import AgentCatalog
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.policy.loader import load_policy
from ralph.policy.validation import validate_agent_chains_satisfiable

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_local_default_chain_lists_only_reference_claude": "the bundled local override template no longer selects agents, so there is no local chain list left to check",
    "test_two_bundled_chain_copies_agree_on_agent_families": "only the global template carries agent chains now; the local override template has none to compare",
}


def _stage_bundled_default(tmp_path: Path, *names: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name in names:
        src = _DEFAULTS_DIR / name
        dst = tmp_path / name
        shutil.copy2(src, dst)
        out[name] = dst
    return out


def _claude_only_registry() -> AgentRegistry:
    claude = AgentConfig(
        cmd="claude",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        json_parser=JsonParserType.CLAUDE,
        session_flag="--resume {}",
        display_name="Claude",
    )
    registry = AgentRegistry(catalog=AgentCatalog())
    registry.register("claude", claude)
    return registry


def test_default_chains_satisfiable_with_claude_only(tmp_path: Path) -> None:
    staged = _stage_bundled_default(tmp_path, "agents.toml", "pipeline.toml", "artifacts.toml")
    bundle = load_policy(staged["agents.toml"].parent)
    validate_agent_chains_satisfiable(bundle, _claude_only_registry())


def test_flat_default_chain_lists_only_reference_claude(tmp_path: Path) -> None:
    staged = _stage_bundled_default(tmp_path, "ralph-workflow.toml")
    data = tomllib.loads(staged["ralph-workflow.toml"].read_text(encoding="utf-8"))
    chains = data["agent_chains"]
    for chain_name, agents in chains.items():
        assert isinstance(agents, list), f"chain '{chain_name}' must be a list of agent names"
        for agent_name in agents:
            assert isinstance(agent_name, str), (
                f"chain '{chain_name}' has non-string agent {agent_name!r}"
            )
            base = agent_name.split("/", maxsplit=1)[0]
            assert base == "claude", (
                f"chain '{chain_name}' references non-claude agent "
                f"{agent_name!r}; the bundled defaults must work with the "
                "default-active claude agent alone so a fresh first-run "
                "user does not need to install another binary."
            )
