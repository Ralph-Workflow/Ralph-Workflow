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

# Built by CROSSING the alias families with several models each, rather
# than listing names by hand. The previous version enumerated 16 chosen
# names and the commit claimed it asserted the no-collision "property";
# it did not -- it contained no ``codex/<model>[effort=...]`` form, and
# every effort variant of one codex model was still sharing a capture.
_BARE_AGENTS = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "pi",
    "cursor",
    "kimi",
)
#: Families whose names are ``<family>/<model>``, with the model forms
#: the shipped ``ralph-workflow.toml`` documents in ``[agent_chains]``.
_DYNAMIC_FAMILIES = {
    "codex": ("gpt-5.4", "gpt-5-codex", "gpt-5.4[effort=low]", "gpt-5.4[effort=high]"),
    "cursor": ("gpt-5", "claude-opus-4-8", "claude-opus-4-8[effort=high]"),
    "pi": ("anthropic/claude-sonnet-4-5", "openai/gpt-5-codex", "anthropic_claude-sonnet-4-5"),
    "kimi": ("kimi-code/k2", "kimi-code/k3-256k"),
    "opencode": ("openai/gpt5", "anthropic/claude", "anthropic_claude"),
    "nanocoder": ("ollama/llama3", "openrouter/qwen", "ollama-llama3"),
    "ccs": ("glm", "mm", "kimi"),
}


def _agent_names() -> list[str]:
    names = list(_BARE_AGENTS)
    for family, models in _DYNAMIC_FAMILIES.items():
        names.extend(f"{family}/{model}" for model in models)
    return names


def _capture_name(name: str) -> str:
    support = default_catalog().get(name)
    config = getattr(support, "config", support)
    assert config is not None, f"{name} did not resolve"
    unit_id = raw_log_unit_id_for(config)
    return raw_log_path_for(Path("/workspace"), unit_id, model=config.model).name


def test_no_two_registry_agents_share_a_capture_file() -> None:
    """The property, over every documented agent form at once."""
    names = _agent_names()
    by_path: dict[str, list[str]] = {}
    for name in names:
        by_path.setdefault(_capture_name(name), []).append(name)

    collisions = {path: shared for path, shared in by_path.items() if len(shared) > 1}

    assert not collisions, f"agents sharing one capture file: {collisions}"
    # Not vacuous: every name really did resolve to a distinct file.
    assert len(by_path) == len(names)


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


def test_effort_variants_of_one_model_are_distinguished() -> None:
    """The suffix changes argv, so it must change the capture.

    ``codex/<model>[effort=high]`` sets ``config.model`` to the bare
    model, and the effort lives only in ``model_flag`` as
    ``-c 'model_reasoning_effort = "high"'``. A fallback gated on
    ``model`` being absent therefore never ran for it, and every effort
    variant of one codex model wrote one file. The form is printed
    verbatim as an example in the shipped ralph-workflow.toml.
    """
    variants = [
        _capture_name(f"codex/gpt-5.4{suffix}")
        for suffix in ("", "[effort=low]", "[effort=high]", "[effort=xhigh]")
    ]

    assert len(set(variants)) == len(variants), variants


def test_two_flags_that_sanitise_alike_stay_apart() -> None:
    """Injectivity survives the filename sanitiser.

    ``safe_id_for`` keeps only ``[0-9A-Za-z._-]`` and collapses runs, so
    a readable token alone cannot be injective: ``--provider ollama
    --model llama3`` and ``--provider ollama-llama3`` both read
    ``ollama-llama3``. That is two agents sharing a capture, which is
    the whole defect class this module guards.
    """
    assert _capture_name("nanocoder/ollama/llama3") != _capture_name("nanocoder/ollama-llama3")
    assert _capture_name("opencode/anthropic/claude") != _capture_name(
        "opencode/anthropic_claude"
    )


def test_an_agent_whose_flag_adds_nothing_keeps_its_plain_name() -> None:
    """No churn where the flag only repeats what ``model`` already says.

    The path appends ``model`` separately, so ``codex/gpt-5.4`` -- whose
    flag is exactly ``--model gpt-5.4`` -- must keep the filename an
    operator already knows.
    """
    assert _capture_name("codex/gpt-5.4") == "codex_gpt-5.4.log"
    assert _capture_name("claude") == "claude.log"


def test_the_model_still_reaches_the_filename() -> None:
    """Not vacuous: the distinguishing token is the model, not a counter."""
    assert "claude-sonnet-4-5" in _capture_name("pi/anthropic/claude-sonnet-4-5")
    assert "gpt-5-codex" in _capture_name("codex/gpt-5-codex")
    assert "glm" in _capture_name("ccs/glm")
    # Readable, not just unique: an operator has to find their transcript.
    assert "gpt-5.4" in _capture_name("codex/gpt-5.4[effort=high]")
