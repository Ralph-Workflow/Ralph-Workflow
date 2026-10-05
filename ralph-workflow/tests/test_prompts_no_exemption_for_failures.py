"""Lock provenance-independent failure ownership in development prompts."""

from __future__ import annotations

from pathlib import Path

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

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


def test_rendered_development_prompts_recover_work_without_expanding_workers(tmp_path: Path) -> None:
    """S-1: six role/continuation/fallback surfaces keep recovery actionable."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    inputs = DeveloperPromptInputs(
        prompt_content="PROMPT PAYLOAD",
        plan_content="PLAN PAYLOAD",
    )
    capabilities = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    recovery_opening = "Huge scope changes execution strategy, not required outcome"
    coordinator_prompts = tuple(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=inputs,
            workspace=workspace,
            session_caps=capabilities,
            template_name=name,
        )
        for name in ("developer_iteration.jinja", "developer_iteration_continuation.jinja")
    )
    worker_inputs = tuple(
        DeveloperPromptInputs(
            prompt_content="PROMPT PAYLOAD",
            plan_content="PLAN PAYLOAD",
            work_unit_id="U-1",
            is_continuation=is_continuation,
        )
        for is_continuation in (False, True)
    )
    worker_prompts = tuple(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=worker_inputs[index],
            workspace=workspace,
            session_caps=capabilities,
            template_name="worker_developer.jinja",
        )
        for index in range(len(worker_inputs))
    )
    broken_context = TemplateContext.default()
    broken_context.registry.register_template("developer_iteration.jinja", "{{ MISSING }}")
    rendering_error_fallback = prompt_developer_iteration_xml_with_context(
        context=broken_context,
        inputs=inputs,
        workspace=workspace,
        session_caps=capabilities,
    )
    broken_worker_context = TemplateContext.default()
    broken_worker_context.registry.register_template("worker_developer.jinja", "{{ MISSING }}")
    worker_rendering_error_fallback = prompt_developer_iteration_xml_with_context(
        context=broken_worker_context,
        inputs=worker_inputs[0],
        workspace=workspace,
        session_caps=capabilities,
        template_name="worker_developer.jinja",
    )

    required_coordinator_text = (
        "On helper failure, inspect evidence and choose an evidence-backed changed tactic",
        "Before transferring ownership, confirm the previous writer has stopped",
        "Continue owned ready work when slots are saturated",
    )
    # Shared size-based rule: a huge plan never justifies a refusal or an
    # assessment-only handoff. Both roles must advance the first safe,
    # testable increment instead of returning a "split this into smaller
    # tasks" reply that performs no work.
    shared_size_only_clause = (
        'Task size alone never justifies a refusal, an assessment-only '
        'handoff, or a "split this into smaller tasks" reply that performs '
        "no work"
    )
    shared_first_increment_clause = "first safe, testable increment"
    # Coordinator-only: decomposition is a tactic, not a request to abandon.
    coordinator_decomposition_clause = (
        "Decomposition is an execution tactic, not a request to abandon"
    )
    # Worker-only: the first-increment rule applies inside the assigned
    # unit, never by widening scope or recursively delegating.
    worker_first_increment_clause = (
        "applies the first-increment rule only within its assigned unit"
    )
    for prompt in (*coordinator_prompts, rendering_error_fallback):
        assert prompt.count(recovery_opening) == 1
        assert prompt.index(recovery_opening) < prompt.index("EXECUTION PLAN")
        for text in required_coordinator_text:
            assert text in prompt
        assert prompt.count(shared_size_only_clause) == 1, (
            "shared size-only rule must appear exactly once on coordinator"
            " surfaces"
        )
        assert (
            prompt.index(shared_size_only_clause) < prompt.index("EXECUTION PLAN")
        )
        assert shared_first_increment_clause in prompt
        assert (
            prompt.index(shared_first_increment_clause)
            < prompt.index("EXECUTION PLAN")
        )
        assert coordinator_decomposition_clause in prompt
        assert worker_first_increment_clause not in prompt
    for prompt in (*worker_prompts, worker_rendering_error_fallback):
        assert prompt.count(recovery_opening) == 1
        assert prompt.index(recovery_opening) < prompt.index("EXECUTION PLAN")
        assert "Workers decompose only their assigned unit" in prompt
        assert "the assigned unit is proven in full" in prompt
        assert "Before transferring ownership" not in prompt
        assert "dispatch disjoint ready scopes" not in prompt
        assert prompt.count(shared_size_only_clause) == 1, (
            "shared size-only rule must appear exactly once on worker surfaces"
        )
        assert (
            prompt.index(shared_size_only_clause) < prompt.index("EXECUTION PLAN")
        )
        assert shared_first_increment_clause in prompt
        assert (
            prompt.index(shared_first_increment_clause)
            < prompt.index("EXECUTION PLAN")
        )
        assert worker_first_increment_clause in prompt
        assert coordinator_decomposition_clause not in prompt


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
