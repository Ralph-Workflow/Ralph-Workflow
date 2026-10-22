"""Policy directories for tests that need a specific conflict-resolution chain."""

from __future__ import annotations

import shutil
from pathlib import Path


def policy_dir_with_resolver_chain(target: Path, chain: str) -> Path:
    """Copy the shipped policy, binding the rebase resolver drain to ``chain``.

    A unique chain name keeps assertions about the once-per-process
    one-agent-chain warning independent of what other tests in the same
    process already loaded.
    """
    defaults = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    config_dir = target / ".agent"
    config_dir.mkdir(parents=True)
    for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
        shutil.copy(defaults / filename, config_dir / filename)
    agents_path = config_dir / "agents.toml"
    text = agents_path.read_text(encoding="utf-8")
    text = text.replace(
        "[agent_chains.rebase_conflict_resolution]", f"[agent_chains.{chain}]"
    ).replace('chain = "rebase_conflict_resolution"', f'chain = "{chain}"')
    agents_path.write_text(text, encoding="utf-8")
    return config_dir
