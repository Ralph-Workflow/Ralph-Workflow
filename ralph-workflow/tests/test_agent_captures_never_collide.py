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

from ralph.agents.catalog import AgentCatalog
from ralph.agents.registry import builtin_supports, register_agent_support_to_catalog
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


def _seeded_catalog() -> AgentCatalog:
    """Return a catalog of the built-in agents, owned by this module.

    NOT ``default_catalog()``. That is a process-wide singleton other
    tests replace and do not restore, so these assertions passed alone
    and failed in a full run with "pi/anthropic/... did not resolve" --
    a test that cannot resolve an agent proves nothing about whether two
    agents collide. Seeding a private catalog makes the census depend on
    the registry's real shapes and on nothing else.
    """
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


def test_the_model_still_reaches_the_filename() -> None:
    """Not vacuous: the distinguishing token is the model, not a counter."""
    assert "claude-sonnet-4-5" in _capture_name("pi/anthropic/claude-sonnet-4-5")
    assert "gpt-5-codex" in _capture_name("codex/gpt-5-codex")
    assert "glm" in _capture_name("ccs/glm")
    # Readable, not just unique: an operator has to find their transcript.
    assert "gpt-5.4" in _capture_name("codex/gpt-5.4[effort=high]")


def test_names_that_differ_only_in_unsafe_characters_stay_apart() -> None:
    """The filename sanitiser folds; the identity must not.

    ``safe_id_for`` keeps only ``[0-9A-Za-z._-]`` and collapses every
    unsafe run to one ``_``, so ``codex/a@b``, ``codex/a_b`` and
    ``codex/a:b`` all became ``codex_a_b.log``. The codex resolver
    accepts any non-space characters, so all three resolve.

    Every branch of the identity has to disambiguate, not just the
    model-flag one: covering that branch alone left headless Claude and
    ``ccs`` folding exactly as before. This defect has now been closed
    four times, each time for the family in front of it, which is why
    this asserts across families rather than within one.
    """
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
    """``--model=x`` carries its value in the SAME token.

    The registry's own resolvers emit ``--model x``, but an operator's
    ``ralph-workflow.toml`` sets ``model_flag`` verbatim, and the equals
    form is ordinary argv. Skipping every dash-led token dropped the
    value with it, so two agents differing only in their model fell back
    to the bare executable and shared one capture.
    """
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_unit_id_for

    alpha = AgentConfig(cmd="pi", model_flag="--model=alpha")
    beta = AgentConfig(cmd="pi", model_flag="--model=beta")

    assert raw_log_unit_id_for(alpha) != raw_log_unit_id_for(beta)
    # Readable, not just distinct: an operator has to find the file.
    assert "alpha" in raw_log_unit_id_for(alpha)
    assert "beta" in raw_log_unit_id_for(beta)


def test_two_names_that_are_both_digest_free_under_the_old_rule_stay_apart() -> None:
    """The pair the previous test structurally could not catch.

    ``test_names_that_differ_only_in_unsafe_characters_stay_apart`` uses
    ``a@b`` / ``a_b`` / ``a:b``. Under the OLD character-class rule only
    ``a_b`` escaped the digest while the other two received one, so the
    paths still differed and reverting the rule left that test green --
    the headline fix of its own commit was unpinned.

    ``gpt5`` and ``_gpt5`` are both digest-free under the old rule and
    both fold to ``codex_gpt5.log``, so this pair fails the moment the
    predicate stops being the sanitiser's true inverse.
    """
    assert _capture_name("codex/gpt5") != _capture_name("codex/_gpt5")
    assert _capture_name("codex/gpt5") != _capture_name("codex/gpt5_")
    assert _capture_name("codex/a_b") != _capture_name("codex/a__b")


def test_an_identity_the_sanitiser_leaves_alone_gets_no_digest() -> None:
    """No churn for names that survive sanitisation untouched.

    ``_`` is legal in a filename component; the sanitiser only collapses
    RUNS of it and strips it from the edges. Testing "every character is
    alphanumeric, ``.`` or ``-``" therefore called ``gpt_5`` lossy and
    renamed a capture that never needed renaming -- the same churn the
    previous round removed for non-Latin letters, reappearing on
    underscores because the rule was restated instead of derived.
    """
    from ralph.display.record_writer import safe_id_for, safe_id_is_lossless

    for survives in ("gpt_5", "a_b", "minimax_M3", "модель", "gpt-5.4", "claude"):
        assert safe_id_is_lossless(survives) is True, survives
        assert safe_id_for(survives) == survives, survives

    for folds in ("_gpt5", "gpt5_", "a__b", "a@b", "a b", ""):
        assert safe_id_is_lossless(folds) is False, folds


def test_the_model_flag_branch_disambiguates_its_model_too() -> None:
    """EVERY branch means every branch, including the one that returns early.

    The model-flag branch returned its own string directly and never
    reached the disambiguation step. Its digest covers ``model_flag``
    alone, and the path appends ``config.model`` separately -- so four
    agents differing only in a model that folds shared one capture.
    """
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(model: str) -> str:
        config = AgentConfig(cmd="mytool", model_flag="--provider acme turbo", model=model)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=model).name

    folding_models = ("anthropic/sonnet", "anthropic:sonnet", "anthropic@sonnet", "anthropic sonnet")
    captures = {capture_for(model) for model in folding_models}

    assert len(captures) == len(folding_models), captures


def test_a_unit_id_carrying_the_join_separator_is_disambiguated() -> None:
    """``safe_id_for`` joins unit id and model with ``_``.

    So an underscore INSIDE the unit id makes the decomposition
    ambiguous even though every character survives sanitisation:
    ``ccs-a_b`` with no model and ``ccs-a`` with model ``b`` both read
    ``ccs-a_b``. Losslessness of the parts is necessary and not
    sufficient; the join has to stay readable too.
    """
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(cmd: str, model: str | None) -> str:
        config = AgentConfig(cmd=cmd, model=model)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=model).name

    # ``ccs <alias>`` resolves to unit id ``ccs-<alias>``, so an alias
    # carrying an underscore puts one inside the unit id.
    joined = capture_for("ccs a_b", None)
    split = capture_for("ccs a", "b")

    assert joined != split, (joined, split)


def test_the_digest_is_wide_enough_to_be_a_disambiguator() -> None:
    """The digest carries the whole no-shared-capture property.

    Every identity that folds under the sanitiser is kept apart by these
    hex characters and nothing else, so their number IS the collision
    resistance. Shrinking the constant to 1 leaves 16 buckets and
    survives every behavioural test, because no fixed set of names
    happens to collide in 16 buckets.
    """
    # Read off the produced name rather than the constant, so this
    # asserts what an operator's directory actually gets.
    left = _capture_name("codex/_gpt5")
    right = _capture_name("codex/gpt5_")

    assert left != right
    assert len(left) == len(right), (left, right)

    digest = left.removeprefix("codex-").removesuffix("_gpt5.log")
    assert len(digest) >= 6, f"fewer than 24 bits is not a disambiguator: {digest!r}"
    assert digest.isalnum(), digest


def test_ccs_alias_tables_do_not_share_the_headless_claude_capture() -> None:
    """The ``[ccs_aliases]`` TABLE form, which the shipped toml prints.

    Those entries set ``cmd = "claude"`` and a ``model_flag`` and leave
    ``model`` unset, so they take the headless-Claude branch -- which
    returned before the model flag was consulted. Every alias, and the
    builtin ``claude-headless`` beside them, wrote one file: four agents
    in one capture, each grading the others' bytes.

    The census above cannot catch this. It enumerates built-in names and
    documented ``family/model`` forms; an alias table is neither, and
    never enters the seeded catalog.
    """
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
    # Readable, not just distinct: an operator has to find the file.
    assert any("sonnet" in name for name in captures), captures


def test_a_dispatcher_alias_with_a_model_flag_is_distinguished() -> None:
    """The other branch that returned before the flag was read."""
    from pathlib import Path

    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(model_flag: str) -> str:
        config = AgentConfig(cmd="ccs glm", model_flag=model_flag)
        return raw_log_path_for(Path("/w"), raw_log_unit_id_for(config), model=None).name

    assert capture_for("--model a") != capture_for("--model b")


def test_agents_differing_only_outside_the_readable_name_stay_apart() -> None:
    """The capture is keyed on the WHOLE invocation.

    Keying on the parts the readable name happens to use closed this
    defect one family at a time for six rounds and never closed the
    class. Three shapes it missed, all reachable from shipped or
    ordinary config:

    * ``[ccs_aliases]`` in its STRING form leaves ``model_flag`` unset,
      so ``ccs/claude`` differed from the builtin ``claude-headless``
      only in its ``cmd`` text.
    * ``ccs/nano`` and the builtin ``nanocoder`` differ only in
      TRANSPORT -- and since the grader exempts interactive-PTY
      transports, the same bytes graded clean or corrupt depending on
      which agent closed the phase. A verdict that depends on anything
      but the bytes is the thing this subsystem promises not to do.
    * Two ``[agents.X]`` entries differing only in a cmd flag.
    """
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    def capture_for(config: AgentConfig) -> str:
        return raw_log_path_for(
            Path("/w"), raw_log_unit_id_for(config), model=config.model
        ).name

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

    # Two agents that ARE the same invocation still share one capture:
    # there is nothing to tell apart, and inventing a difference would
    # split one agent's transcript across two files.
    same = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    assert capture_for(same) == capture_for(
        AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    )


def test_a_capture_name_is_readable_and_stable() -> None:
    """Every identity now carries a digest of its whole invocation.

    That is a deliberate trade, and it costs a one-time rename of every
    capture file. Keying on only the parts the readable name uses closed
    this defect family by family for six rounds -- headless Claude, then
    ccs aliases, then dynamic families, then effort suffixes, then the
    alias TABLE form, then the alias STRING form -- while the class
    stayed open. The digest closes the class.

    What must survive the trade: an operator can still recognise the
    file, and the same agent always writes the same one.
    """
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
    # Stable: the same invocation, twice, is the same file.
    assert capture_for() == name
