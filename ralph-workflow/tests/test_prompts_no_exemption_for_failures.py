"""Lock provenance-independent failure ownership in development prompts.

Also pins the large-scope contract: a development iteration whose work
proves far larger than the plan indicated is a scheduling signal, never
an exit condition. The wording is single-sourced in the
``shared/_developer_iteration_guidance.j2``, ``shared/_parallel_execution.j2``
(``.jinja`` on disk, ``.j2`` to Jinja), and
``shared/_no_exemption_for_failures.j2`` partials.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

from ralph.prompts.template_context import TemplateContext

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_PARTIAL = _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2"
_GUIDANCE_PARTIAL = _TEMPLATES_DIR / "shared" / "_developer_iteration_guidance.j2"
_PARALLEL_PARTIAL = _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja"
_TEMPLATE_NAMES = (
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
    "worker_developer.jinja",
    "development_analysis.jinja",
)
_INCLUDE = "{% include 'shared/_no_exemption_for_failures.j2' %}"
_PARALLEL_INCLUDE = "{% include 'shared/_parallel_execution.j2' %}"
_PARALLEL_HEADING = "## PARALLEL EXECUTION (declared units and independent ready steps)"
_REQUIRED_PHRASES = (
    "ALL issues must be resolved",
    "no such thing as a blocking issue",
    "no such thing as a pre-existing issue",
    "MUST resolve anything that comes up",
    "whether or not you caused it",
)


def test_failure_ownership_partial_contains_all_required_phrases() -> None:
    source = _PARTIAL.read_text(encoding="utf-8")

    for phrase in _REQUIRED_PHRASES:
        assert phrase in source


def test_all_development_prompts_include_failure_ownership_partial() -> None:
    for name in _TEMPLATE_NAMES:
        source = (_TEMPLATES_DIR / name).read_text(encoding="utf-8")
        assert _INCLUDE in source


def test_development_analysis_keeps_verdicts_independent_of_failure_provenance() -> None:
    source = (_TEMPLATES_DIR / "development_analysis.jinja").read_text(encoding="utf-8")

    assert "verdict is independent of who caused the failure" in source
    assert "not met stays not met" in source


def test_no_template_sentence_starts_lowercase() -> None:
    """S-13 (a): no sentence in the touched templates starts lowercase.

    The S-13 brief names exactly seven touched files: the four shared partials
    plus worker_developer.jinja, the developer iteration guidance partial,
    and the parallel execution partial. Other templates are out of scope for
    this consolidation.
    """
    import re
    touched = (
        _TEMPLATES_DIR / "worker_developer.jinja",
        _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2",
        _TEMPLATES_DIR / "shared" / "_run_budget.j2",
        _TEMPLATES_DIR / "shared" / "_verification_commitments.j2",
        _TEMPLATES_DIR / "shared" / "_development_result_proof.jinja",
        _TEMPLATES_DIR / "shared" / "_developer_iteration_guidance.j2",
        _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja",
    )
    for path in touched:
        source = path.read_text(encoding="utf-8")
        # Strip Jinja control lines so `{% if ... %}` and `{% include ... %}`
        # don't count as sentences.
        stripped = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("{%")
        )
        for raw_sentence in re.split(r"(?<=[.!?])\s+", stripped):
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            first = sentence[0]
            if not first.isalpha():
                continue
            assert first.isupper(), (
                f"sentence starts lowercase in {path.relative_to(_TEMPLATES_DIR)}: "
                f"{sentence[:80]!r}"
            )


def test_partial_results_rule_is_single_sourced() -> None:
    """S-13 (b): the partial-results-are-last-resort rule lives in
    `_no_exemption_for_failures.j2`; the other partials reference it via the
    shared `{% include %}` so the wording stays in lockstep.
    """
    partials = (
        _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2",
        _TEMPLATES_DIR / "shared" / "_run_budget.j2",
        _TEMPLATES_DIR / "shared" / "_verification_commitments.j2",
        _TEMPLATES_DIR / "shared" / "_development_result_proof.jinja",
    )
    sources = {p.name: p.read_text(encoding="utf-8") for p in partials}

    # The canonical home keeps the full rule.
    canonical = sources["_no_exemption_for_failures.j2"]
    assert "no such thing as a pre-existing issue" in canonical
    assert "MUST resolve anything that comes up" in canonical

    # The other partials do not restate the rule in full - they reference it.
    for name, text in sources.items():
        if name == "_no_exemption_for_failures.j2":
            continue
        assert "no such thing as a pre-existing issue" not in text, (
            f"{name} restates the rule; it should reference the canonical home"
        )
        assert "MUST resolve anything that comes up" not in text, (
            f"{name} restates the rule; it should reference the canonical home"
        )


# ---------------------------------------------------------------------------
# Large-scope contract: a development iteration whose work proves far larger
# than the plan indicated is a scheduling signal, never an exit condition.
# The wording is single-sourced across three shared partials so every
# development surface inherits it.
# ---------------------------------------------------------------------------


def test_guidance_declares_large_scope_a_dispatch_signal() -> None:
    """`_developer_iteration_guidance.j2` names oversized scope a dispatch
    signal, never an exit condition, and points readers at the canonical
    exemption rule for the strict "never justifies partial" wording."""
    source = _GUIDANCE_PARTIAL.read_text(encoding="utf-8")

    assert "## Large scope is a dispatch signal, not a stop signal" in source
    # Position: after the "Completion is the default outcome" section and
    # before the "Plan fidelity" section.
    assert source.index("Completion is the default outcome") < source.index(
        "## Large scope is a dispatch signal, not a stop signal"
    )
    assert source.index("## Large scope is a dispatch signal, not a stop signal") < source.index(
        "## Plan fidelity"
    )
    for phrase in (
        "never an exit condition",
        "re-cut the remainder",
        "zero verified work",
        "execution defect",
        "shared/_no_exemption_for_failures.j2",
    ):
        assert phrase in source, phrase


def test_large_scope_guidance_splits_main_and_worker_roles() -> None:
    """The large-scope section branches on ``IS_WORKER`` so workers keep
    working their assigned unit and the main session re-cuts the remainder
    and dispatches concurrently."""
    guidance = TemplateContext.default().registry.get_template(
        "shared/_developer_iteration_guidance"
    )
    template = Environment().from_string(guidance)
    main = template.render(IS_WORKER=False)
    worker = template.render(IS_WORKER=True)

    assert "re-cut the remainder" in main
    assert "re-cut the remainder" not in worker
    assert "verified increments" in worker


def test_parallel_execution_adds_scope_size_escalation() -> None:
    """`_parallel_execution.jinja` adds a "Scope-size escalation" subsection
    that escalates an oversized remaining scope into a parallel dispatch
    rather than trimming required work."""
    source = _PARALLEL_PARTIAL.read_text(encoding="utf-8")

    assert "### Scope-size escalation" in source
    # Position: after the ready-group section and before the per-unit brief.
    assert source.index("### Independent ready group (for linear plans too)") < source.index(
        "### Scope-size escalation"
    )
    assert source.index("### Scope-size escalation") < source.index(
        "### Per-unit subagent brief (when units exist)"
    )
    for phrase in (
        "dispatch failure",
        "Keep the dispatch pipeline full",
        "instead of trimming required work",
    ):
        assert phrase in source, phrase


def test_no_exemption_names_remaining_work_size() -> None:
    """`_no_exemption_for_failures.j2` lists the sheer size of the remaining
    work alongside the other never-justifies-partial reasons."""
    source = _PARTIAL.read_text(encoding="utf-8")
    assert "size of the remaining work never qualifies" in source


def test_first_three_development_templates_include_parallel_execution_partial() -> None:
    """The first three development templates pull the parallel-execution
    partial via the literal ``{% include 'shared/_parallel_execution.j2' %}``
    string so the scope-size escalation is single-sourced."""
    for name in (
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ):
        source = (_TEMPLATES_DIR / name).read_text(encoding="utf-8")
        assert _PARALLEL_INCLUDE in source, name
        # The heading above the include is the documented surface.
        assert _PARALLEL_HEADING in source, name


def test_rendered_developer_prompt_carries_large_scope_contract() -> None:
    """The materialized developer prompt carries the large-scope contract
    end-to-end: the main-session guidance section and the parallel-execution
    scope-size escalation both reach the rendered text."""
    import tempfile

    from ralph.prompts.developer import (
        DeveloperPromptInputs,
        prompt_developer_iteration_xml_with_context,
    )
    from ralph.prompts.types import SessionCapabilities, SessionDrain
    from ralph.workspace.memory import MemoryWorkspace

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        workspace = MemoryWorkspace(root=str(tmp_path))
        prompt = prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement it", plan_content="### [S-1] Change it"
            ),
            workspace=workspace,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        )

    assert "Large scope is a dispatch signal" in prompt
    assert "Scope-size escalation" in prompt
