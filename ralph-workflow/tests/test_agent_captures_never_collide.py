"""No two agents may write one raw capture file.

The capture path is keyed ``(unit_id, config.model)``. One raw capture
already accumulates every retry and every phase for a given agent, so a
verdict computed over it is only meaningful if the file belongs to that
agent alone. When two agents share it, one phase's verdict grades the
other's bytes and quotes the other's transport failures.

This collision has been found and "closed" three times in three
different families -- headless Claude vs interactive Claude, then every
``ccs/<alias>``, then five dynamic-alias families whose resolvers set
``model_flag`` but leave ``model`` as None so the key degenerated to the
bare executable. Each fix closed one family. This test asserts the
PROPERTY over the registry itself, so the next family cannot reintroduce
it quietly.
"""

from __future__ import annotations

from pathlib import Path

from ralph.agents.registry import default_catalog
from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

# Every form the shipped ralph-workflow.toml documents in [agent_chains],
# plus the two families whose collisions were previously closed.
_AGENT_NAMES = (
    "pi/anthropic/claude-sonnet-4-5",
    "pi/openai/gpt-5-codex",
    "cursor/claude-opus-4-8",
    "cursor/gpt-5",
    "kimi/kimi-code/k3-256k",
    "kimi/kimi-code/k2",
    "opencode/anthropic/claude",
    "opencode/openai/gpt5",
    "nanocoder/ollama/llama3",
    "nanocoder/openrouter/qwen",
    "codex/gpt-5-codex",
    "ccs/glm",
    "ccs/mm",
    "claude",
    "claude-headless",
    "kimi",
)


def _capture_name(name: str) -> str:
    support = default_catalog().get(name)
    config = getattr(support, "config", support)
    assert config is not None, f"{name} did not resolve"
    unit_id = raw_log_unit_id_for(config)
    return raw_log_path_for(Path("/workspace"), unit_id, model=config.model).name


def test_no_two_registry_agents_share_a_capture_file() -> None:
    """The property, over every documented agent form at once."""
    by_path: dict[str, list[str]] = {}
    for name in _AGENT_NAMES:
        by_path.setdefault(_capture_name(name), []).append(name)

    collisions = {path: names for path, names in by_path.items() if len(names) > 1}

    assert not collisions, f"agents sharing one capture file: {collisions}"


def test_two_models_of_one_executable_are_distinguished() -> None:
    """The specific shape that regressed: same binary, different model.

    ``pi/anthropic/...`` and ``pi/openai/...`` both run the ``pi``
    executable and differ only in ``model_flag``. A chain listing one per
    phase, or two as fallbacks within a phase, is ordinary configuration.
    """
    assert _capture_name("pi/anthropic/claude-sonnet-4-5") != _capture_name(
        "pi/openai/gpt-5-codex"
    )
    assert _capture_name("opencode/anthropic/claude") != _capture_name("opencode/openai/gpt5")
    assert _capture_name("nanocoder/ollama/llama3") != _capture_name("nanocoder/openrouter/qwen")


def test_the_model_still_reaches_the_filename() -> None:
    """Not vacuous: the distinguishing token is the model, not a counter."""
    assert "claude-sonnet-4-5" in _capture_name("pi/anthropic/claude-sonnet-4-5")
    assert "gpt-5-codex" in _capture_name("codex/gpt-5-codex")
    assert "glm" in _capture_name("ccs/glm")
