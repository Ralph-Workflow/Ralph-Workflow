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


_RECOVERY_MARKERS = (
    "Inventory the remaining references",
    "falsifiable increment",
    "changed, evidence-backed approach",
)


def _render_recovery_surface(
    tmp_path: Path,
    *,
    template_name: str,
    worker: bool = False,
    context: TemplateContext,
    continuation: bool = False,
) -> str:
    inputs = DeveloperPromptInputs(
        prompt_content="Implement the requested behavior.",
        plan_content="### [S-1] Deliver the behavior",
        work_unit_id="prompt-tests" if worker else "",
        work_unit_description="Deliver the assigned prompt test changes." if worker else "",
        work_unit_directories="ralph-workflow/tests" if worker else "",
        worker_namespace="/tmp/.agent/workers/prompt-tests" if worker else "",
        is_continuation=(
            continuation or template_name == "developer_iteration_continuation.jinja"
        ),
    )
    return prompt_developer_iteration_xml_with_context(
        context=context,
        inputs=inputs,
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )


def test_developer_prompt_regression_scope_recovery_is_early_once_and_role_scoped(
    tmp_path: Path,
) -> None:
    context = TemplateContext.default()
    surfaces = (
        ("developer_iteration.jinja", False, False),
        ("developer_iteration_continuation.jinja", False, True),
        ("developer_iteration_fallback.jinja", False, False),
        ("worker_developer.jinja", True, False),
        ("worker_developer.jinja", True, True),
        ("developer_iteration_fallback.jinja", True, False),
    )
    for template_name, worker, continuation in surfaces:
        rendered = _render_recovery_surface(
            tmp_path,
            template_name=template_name,
            worker=worker,
            context=context,
            continuation=continuation,
        )
        assert rendered.count("## Scope-size recovery procedure") == 1
        guidance_at = rendered.index("## Scope-size recovery procedure")
        payload_at = min(
            rendered.index(marker)
            for marker in ("PROMPT:", "ORIGINAL REQUEST:", "EXECUTION PLAN:")
            if marker in rendered
        )
        assert guidance_at < payload_at
        for marker in _RECOVERY_MARKERS:
            assert marker in rendered
        assert "safe concrete continuation requires" not in rendered
        assert "report partial progress" not in rendered
        if worker:
            assert "Workers never recursively dispatch" in rendered
            assert "exact assigned ownership" in rendered
            assert "one-unit result" in rendered
            assert "full repository-wide gate (" in rendered
            assert "run its focused proof" in rendered
            assert "reassess readiness" in rendered
            assert "until the assigned unit is fully proven" in rendered
            assert "Coordinators assign disjoint ownership" not in rendered
            if continuation:
                assert "Before submitting a continuation" in rendered
                assert "you MUST NOT submit the artifact or declare completion" in rendered
        else:
            assert "assign disjoint ownership" in rendered
            assert "implement its own ready critical-path work" in rendered
            assert "queue later work in waves" in rendered
            assert "continue ready work sequentially" in rendered


def test_developer_prompt_regression_rendering_failure_uses_recovery_fallback(
    tmp_path: Path,
) -> None:
    context = TemplateContext.default()
    context.registry.register_template("broken.jinja", "{% invalid %}")
    rendered = prompt_developer_iteration_xml_with_context(
        context=context,
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it.",
            plan_content="### [S-1] Deliver it",
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="broken.jinja",
    )

    assert rendered.count("## Scope-size recovery procedure") == 1
    assert rendered.index("## Scope-size recovery procedure") < rendered.index("ORIGINAL REQUEST:")
    assert "continue ready work sequentially" in rendered
