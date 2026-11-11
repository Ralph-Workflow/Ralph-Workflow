from __future__ import annotations

from pathlib import Path

from ralph.agents.catalog import AgentCatalog
from ralph.agents.registry import builtin_supports, register_agent_support_to_catalog
from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

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


def _seeded_catalog() -> AgentCatalog:
    catalog = AgentCatalog()
    for support in builtin_supports():
        register_agent_support_to_catalog(support.name, support, catalog)
    return catalog


def _capture_name(name: str) -> str:
    support = _seeded_catalog().get(name)
    config = getattr(support, "config", support)
    assert config is not None, f"{name} did not resolve"
    unit_id = raw_log_unit_id_for(config)
    return raw_log_path_for(Path("/workspace"), unit_id, model=config.model).name


def test_no_two_registry_agents_share_a_capture_file() -> None:
    names = _agent_names()
    by_path: dict[str, list[str]] = {}
    for name in names:
        by_path.setdefault(_capture_name(name), []).append(name)

    collisions = {path: shared for path, shared in by_path.items() if len(shared) > 1}

    assert not collisions, f"agents sharing one capture file: {collisions}"
    assert len(by_path) == len(names)


def test_two_models_of_one_executable_are_distinguished() -> None:
    assert _capture_name("pi/anthropic/claude-sonnet-4-5") != _capture_name("pi/openai/gpt-5-codex")
    assert _capture_name("opencode/anthropic/claude") != _capture_name("opencode/openai/gpt5")
    assert _capture_name("nanocoder/ollama/llama3") != _capture_name("nanocoder/openrouter/qwen")


def test_effort_variants_of_one_model_are_distinguished() -> None:
    variants = [
        _capture_name(f"codex/gpt-5.4{suffix}")
        for suffix in ("", "[effort=low]", "[effort=high]", "[effort=xhigh]")
    ]

    assert len(set(variants)) == len(variants), variants


def test_two_flags_that_sanitise_alike_stay_apart() -> None:
    assert _capture_name("nanocoder/ollama/llama3") != _capture_name("nanocoder/ollama-llama3")
    assert _capture_name("opencode/anthropic/claude") != _capture_name("opencode/anthropic_claude")


def test_the_model_still_reaches_the_filename() -> None:
    assert "claude-sonnet-4-5" in _capture_name("pi/anthropic/claude-sonnet-4-5")
    assert "gpt-5-codex" in _capture_name("codex/gpt-5-codex")
    assert "glm" in _capture_name("ccs/glm")
    assert "gpt-5.4" in _capture_name("codex/gpt-5.4[effort=high]")


def test_names_that_differ_only_in_unsafe_characters_stay_apart() -> None:
    families = ("codex", "claude", "claude-headless", "ccs")
    variants = ("a@b", "a_b", "a:b")

    names = [f"{family}/{variant}" for family in families for variant in variants]
    by_path: dict[str, list[str]] = {}
    for name in names:
        support = _seeded_catalog().get(name)
        if getattr(support, "config", support) is None:
            continue
        by_path.setdefault(_capture_name(name), []).append(name)

    collisions = {path: shared for path, shared in by_path.items() if len(shared) > 1}

    assert not collisions, f"agents sharing one capture file: {collisions}"


def test_a_model_flag_using_equals_still_distinguishes_agents() -> None:
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_unit_id_for

    alpha = AgentConfig(cmd="pi", model_flag="--model=alpha")
    beta = AgentConfig(cmd="pi", model_flag="--model=beta")

    assert raw_log_unit_id_for(alpha) != raw_log_unit_id_for(beta)
    assert "alpha" in raw_log_unit_id_for(alpha)
    assert "beta" in raw_log_unit_id_for(beta)


def test_two_names_that_are_both_digest_free_under_the_old_rule_stay_apart() -> None:
    assert _capture_name("codex/gpt5") != _capture_name("codex/_gpt5")
    assert _capture_name("codex/gpt5") != _capture_name("codex/gpt5_")
    assert _capture_name("codex/a_b") != _capture_name("codex/a__b")


def test_an_identity_the_sanitiser_leaves_alone_gets_no_digest() -> None:
    from ralph.display.record_writer import safe_id_for, safe_id_is_lossless

    for survives in ("gpt_5", "a_b", "minimax_M3", "модель", "gpt-5.4", "claude"):
        assert safe_id_is_lossless(survives) is True, survives
        assert safe_id_for(survives) == survives, survives

    for folds in ("_gpt5", "gpt5_", "a__b", "a@b", "a b", ""):
        assert safe_id_is_lossless(folds) is False, folds


def test_the_model_flag_branch_disambiguates_its_model_too() -> None:
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(model: str) -> str:
        config = AgentConfig(cmd="mytool", model_flag="--provider acme turbo", model=model)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=model).name

    folding_models = (
        "anthropic/sonnet",
        "anthropic:sonnet",
        "anthropic@sonnet",
        "anthropic sonnet",
    )
    captures = {capture_for(model) for model in folding_models}

    assert len(captures) == len(folding_models), captures


def test_a_unit_id_carrying_the_join_separator_is_disambiguated() -> None:
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(cmd: str, model: str | None) -> str:
        config = AgentConfig(cmd=cmd, model=model)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=model).name

    joined = capture_for("ccs a_b", None)
    split = capture_for("ccs a", "b")

    assert joined != split, (joined, split)


def test_the_digest_is_wide_enough_to_be_a_disambiguator() -> None:
    left = _capture_name("codex/_gpt5")
    right = _capture_name("codex/gpt5_")

    assert left != right
    assert len(left) == len(right), (left, right)

    digest = left.removeprefix("codex-").removesuffix("_gpt5.log")
    assert len(digest) >= 6, f"fewer than 24 bits is not a disambiguator: {digest!r}"
    assert digest.isalnum(), digest


def test_ccs_alias_tables_do_not_share_the_headless_claude_capture() -> None:
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(cmd: str, model_flag: str | None) -> str:
        config = AgentConfig(
            cmd=cmd, model_flag=model_flag, output_flag="--output-format=stream-json"
        )
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=None).name

    captures = {
        capture_for("claude", "--model claude-sonnet-4"),
        capture_for("claude", "--model claude-opus-4"),
        capture_for("claude", "--model claude-haiku-4"),
        capture_for("claude -p", None),
    }

    assert len(captures) == 4, captures
    assert any("sonnet" in name for name in captures), captures


def test_a_dispatcher_alias_with_a_model_flag_is_distinguished() -> None:
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(model_flag: str) -> str:
        config = AgentConfig(cmd="ccs glm", model_flag=model_flag)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=None).name

    assert capture_for("--model a") != capture_for("--model b")


def test_agents_differing_only_outside_the_readable_name_stay_apart() -> None:
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(config: AgentConfig) -> str:
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=config.model).name

    stream_json = "--output-format=stream-json"
    agents = {
        "builtin claude-headless": AgentConfig(
            cmd="claude -p", output_flag=stream_json, transport=AgentTransport.CLAUDE
        ),
        "ccs/claude string form": AgentConfig(
            cmd="claude", output_flag=stream_json, transport=AgentTransport.CLAUDE
        ),
        "builtin nanocoder": AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER),
        "ccs/nano string form": AgentConfig(cmd="nanocoder", transport=AgentTransport.CLAUDE),
        "claude --permission-mode plan": AgentConfig(
            cmd="claude --permission-mode plan", transport=AgentTransport.CLAUDE_INTERACTIVE
        ),
        "claude --dangerously-skip": AgentConfig(
            cmd="claude --dangerously-skip-permissions",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        ),
        "builtin claude": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
    }

    by_capture: dict[str, list[str]] = {}
    for label, config in agents.items():
        by_capture.setdefault(capture_for(config), []).append(label)

    collisions = {path: shared for path, shared in by_capture.items() if len(shared) > 1}
    assert not collisions, collisions

    same = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    assert capture_for(same) == capture_for(
        AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    )


def test_a_capture_name_is_readable_and_stable() -> None:
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for() -> str:
        config = AgentConfig(
            cmd="codex exec",
            model="gpt-5.4",
            output_flag="--json",
            transport=AgentTransport.CODEX,
        )
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model="gpt-5.4").name

    name = capture_for()

    assert name.startswith("codex-"), name
    assert "gpt-5.4" in name, name
    assert name.endswith(".log"), name
    assert capture_for() == name
