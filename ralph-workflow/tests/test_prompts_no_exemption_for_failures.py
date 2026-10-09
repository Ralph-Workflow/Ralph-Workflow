"""Lock provenance-independent failure ownership in development prompts."""

from __future__ import annotations

from pathlib import Path

import pytest

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
            line for line in source.splitlines() if not line.lstrip().startswith("{%")
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


_SIZE_RULE = "Task size alone must not produce an assessment-only handoff"
_FIRST_INCREMENT_RULE = "Select and perform the first safe, testable increment"
# The old partial-progress escape clause that the S-1 / U-1 work MUST
# remove. Surface area: every rendered coordinator and worker development
# prompt. Phrased verbatim from the pre-change guidance partial so a
# regression to the old wording is loud, not silent.
_OLD_PARTIAL_PROGRESS_ESCAPE = (
    "retry only with a new evidence-based hypothesis or report partial progress"
)
# Actionable recovery clauses the strengthened guidance MUST carry on every
# surface. At least one must render; a local failure must never be a
# permission slip to abandon the plan.
_ACTIONABLE_RECOVERY_CLAUSES = (
    "narrower increment",
    "continue other independent ready work",
    "new evidence-based hypothesis",
)
# Worker-only assignment-local recovery clause the shared guidance MUST keep
# visible on every worker-rendered surface, so workers do not lose the
# "within your assignment" framing once the partial-progress escape is
# removed.
_WORKER_ASSIGNMENT_LOCAL_CLAUSES = (
    "within your assignment",
    "the assigned unit",
    "your scope",
)
# Coordinator-only pre-submit review mandate the continuation/first-iteration
# templates must NOT leak to worker renderings.
_COORDINATOR_REVIEW_MANDATE = "independent read-only sub-agent"


@pytest.mark.parametrize(
    ("template_name", "is_worker"),
    (
        ("developer_iteration.jinja", False),
        ("developer_iteration_continuation.jinja", False),
        ("developer_iteration_fallback.jinja", False),
    ),
)
def test_rendered_development_surfaces_require_size_based_execution(
    tmp_path: Path, template_name: str, *, is_worker: bool
) -> None:
    """Regression: large-scope tasks must not produce a zero-work handoff.

    The shared guidance must place the size-based prohibition and the
    first-increment instruction before ``EXECUTION PLAN`` on every
    coordinator/worker fresh, continuation, and fallback surface, and they
    must appear exactly once per surface. Workers stay assignment-scoped and
    do not receive the coordinator's dispatch directive.
    """
    rendered = " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
                work_unit_id="unit" if is_worker else "",
                work_unit_description="Implement the assigned change" if is_worker else "",
                work_unit_directories="src" if is_worker else "",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        ).split()
    )

    assert rendered.count(_SIZE_RULE) == 1
    assert rendered.count(_FIRST_INCREMENT_RULE) == 1
    assert rendered.index(_SIZE_RULE) < rendered.index("EXECUTION PLAN")
    if is_worker:
        # Workers must not receive the coordinator's dispatch directive.
        # The old literal ("dispatch independent ready groups") was the
        # optional-dispatch opener; the parallel-by-default rewrite
        # replaced it with the mandatory "dispatch every ready unit
        # concurrently in one wave" wording, which is also coordinator-
        # only and must stay out of the worker rendering.
        assert "dispatch independent ready groups" not in rendered
        assert "dispatch every ready unit concurrently" not in rendered
        assert "stopped-writer transfer" not in rendered
        assert _COORDINATOR_REVIEW_MANDATE not in rendered
    else:
        # Coordinator surfaces must state the parallel-by-default
        # dispatch directive verbatim; this is the shared wording
        # contract anchor that proves the rewrite.
        assert "dispatch every ready unit concurrently" in rendered


@pytest.mark.parametrize(
    ("template_name", "is_worker"),
    (
        ("developer_iteration.jinja", False),
        ("developer_iteration_continuation.jinja", False),
        ("developer_iteration_fallback.jinja", False),
    ),
)
def test_rendered_development_surfaces_replace_partial_progress_escape_with_recovery_loop(
    tmp_path: Path, template_name: str, *, is_worker: bool
) -> None:
    """S-1/S-2: every rendered surface must replace the old partial-progress
    escape with an actionable recovery loop and keep role-appropriate
    prose. The old escape phrase is the unique verbatim string that bound
    the prior guidance; removing it is the contract U-1 implements.
    """
    rendered = " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
                work_unit_id="unit" if is_worker else "",
                work_unit_description="Implement the assigned change" if is_worker else "",
                work_unit_directories="src" if is_worker else "",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        ).split()
    )

    # The unconditional partial-progress escape is gone from every surface.
    assert _OLD_PARTIAL_PROGRESS_ESCAPE not in rendered, (
        f"stale partial-progress escape in {template_name} (is_worker={is_worker})"
    )

    # Actionable recovery replaces the escape on every surface — at least
    # one of the recovery clauses must be present, and a local failure
    # must never read as a permission to abandon the plan.
    rendered_lower = rendered.lower()
    assert any(clause in rendered for clause in _ACTIONABLE_RECOVERY_CLAUSES), (
        f"no actionable recovery clause rendered for {template_name} (is_worker={is_worker})"
    )
    # "local failure" / "ready work" co-occurrence rejects any rephrasing
    # that still treats a local failure as a global stop signal.
    assert "local failure" in rendered_lower, (
        f"local-failure framing missing in {template_name} (is_worker={is_worker})"
    )

    if is_worker:
        # Workers never get coordinator dispatch or coordinator pre-submit
        # review mandates — and the partial-progress escape must not be
        # replaced by a worker-only escape either. The dispatch directive
        # (old "dispatch independent ready groups" OR new "dispatch every
        # ready unit concurrently") is coordinator-only and must not
        # leak into a worker rendering.
        assert "dispatch independent ready groups" not in rendered
        assert "dispatch every ready unit concurrently" not in rendered
        assert "stopped-writer transfer" not in rendered
        assert _COORDINATOR_REVIEW_MANDATE not in rendered
        assert any(clause in rendered for clause in _WORKER_ASSIGNMENT_LOCAL_CLAUSES), (
            f"worker lost assignment-local recovery framing in {template_name}"
        )
