"""Lock provenance-independent failure ownership in development prompts."""

from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_PARTIAL = _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2"
_TEMPLATE_NAMES = (
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
    "worker_developer.jinja",
    "development_analysis.jinja",
)
_INCLUDE = "{% include 'shared/_no_exemption_for_failures.j2' %}"
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

    The S-13 brief names exactly five touched files: the four shared partials
    plus worker_developer.jinja. Other templates are out of scope for this
    consolidation.
    """
    import re
    touched = (
        _TEMPLATES_DIR / "worker_developer.jinja",
        _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2",
        _TEMPLATES_DIR / "shared" / "_run_budget.j2",
        _TEMPLATES_DIR / "shared" / "_verification_commitments.j2",
        _TEMPLATES_DIR / "shared" / "_development_result_proof.jinja",
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


def test_developer_prompts_regression_early_scope_recovery_matrix() -> None:
    """S-1/S-2: every delivered role recovers before request/plan payloads."""
    from ralph.mcp.protocol.capability_mapping import SessionDrain
    from ralph.prompts.developer import (
        DeveloperPromptInputs,
        prompt_developer_iteration_xml_with_context,
    )
    from ralph.prompts.template_context import TemplateContext
    from ralph.prompts.types import SessionCapabilities
    from ralph.workspace.memory import MemoryWorkspace

    surfaces = (
        ("developer_iteration.jinja", False, False, False),
        ("developer_iteration_continuation.jinja", False, True, False),
        ("developer_iteration_fallback.jinja", False, False, False),
        ("worker_developer.jinja", True, False, False),
        ("worker_developer.jinja", True, True, False),
        ("developer_iteration_fallback.jinja", True, False, False),
        ("developer_iteration_fallback.jinja", True, True, False),
        ("developer_iteration.jinja", False, False, True),
        ("worker_developer.jinja", True, True, True),
    )
    for template, worker, continuation, render_failure in surfaces:
        context = TemplateContext.default()
        if render_failure:
            context.registry.register_template(template, "{% invalid_tag %}")
        rendered = prompt_developer_iteration_xml_with_context(
            context,
            DeveloperPromptInputs(
                prompt_content="",
                plan_content="",
                product_criteria_path="/request-payload-marker.md",
                plan_path="/plan-payload-marker.md",
                is_continuation=continuation,
                work_unit_id="U-1" if worker else "",
                work_unit_description="Implement assigned recovery changes.",
                work_unit_paths="src/recovery.py" if worker else "",
                worker_namespace=".agent/workers/U-1" if worker else "",
            ),
            MemoryWorkspace(),
            SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template,
        )
        normalized = " ".join(rendered.split())
        label = (template, worker, continuation, render_failure)
        mandate = "## Scope recovery"
        assert rendered.count(mandate) == 1, label
        assert rendered.index(mandate) < rendered.index("/request-payload-marker.md"), label
        assert rendered.index(mandate) < rendered.index("/plan-payload-marker.md"), label
        for action in (
            "before any implementation",
            "inventory",
            "falsifiable increment",
            "implement",
            "verify",
            "recompute readiness",
            "changed, evidence-backed approach",
            "safe work remains",
            "shared/_no_exemption_for_failures.j2",
        ):
            assert action in normalized, (label, action)
        for forbidden in (
            "or report partial progress",
            "Then return the unit result.",
            "If the unit remains blocked, submit",
            "A blocked item with a safe concrete continuation requires",
            "a blocked assignment requires a partial result",
        ):
            assert forbidden not in normalized, (label, forbidden)
        if worker:
            for obligation in (
                "Workers never dispatch sub-agents",
                "focused verification",
                "only the assigned",
                "src/recovery.py",
                ".agent/workers/U-1/artifacts/development_result.md",
                ".agent/workers/U-1/tmp/development_result.md",
            ):
                assert obligation in normalized, (label, obligation)
            assert "dispatch independent ready scopes" not in normalized, label
        else:
            for obligation in (
                "dispatch independent ready scopes",
                "sequentially",
                "permissions",
                "exposed capacity",
                "acceptance criteria",
            ):
                assert obligation in normalized, (label, obligation)
