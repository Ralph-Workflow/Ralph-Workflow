"""Lock provenance-independent failure ownership in development prompts.

The recovery-loop regression contract below (S-1 in the development plan)
asserts the development prompts are engineered so a huge or underestimated
task triggers decomposition, parallel dispatch when possible, and continued
ready-work execution when sub-agent tooling is unavailable. Difficulty,
an unavailable tool, a saturated slot, a failed tactic, or a tight deadline
must not alone authorize stopping.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_PARTIAL = _TEMPLATES_DIR / "shared" / "_no_exemption_for_failures.j2"
_DEVELOPER_GUIDANCE = _TEMPLATES_DIR / "shared" / "_developer_iteration_guidance.j2"
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
        for raw_sentence in cast("list[str]", re.split(r"(?<=[.!?])\s+", stripped)):
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


# MERGE: consolidate source-order and duplicate recovery renders into the
# delivered-role matrix. Owner: prompt tests; trigger: development templates;
# lane: default; cost: bounded in-process renders; evidence: focused pytest;
# lifecycle: revise when delivered role contracts change. Keep independent
# provenance, deadline, worker-artifact, and canonical-rule defenses.
def _recovery_inputs(
    root: Path, *, worker: bool, continuation: bool, file_plan: bool
) -> DeveloperPromptInputs:
    return DeveloperPromptInputs(
        prompt_content="REQUEST-CONTENT-MARKER",
        product_criteria_path=str(root / "REQUEST-FILE-MARKER.md"),
        plan_content="PLAN-CONTENT-MARKER",
        plan_path=str(root / "PLAN-FILE-MARKER.md") if file_plan else "",
        work_unit_id="unit-a" if worker else "",
        work_unit_description="Implement the assigned unit.",
        work_unit_directories="src/unit",
        work_unit_paths="src/exact.py",
        worker_namespace=str(root / ".agent/workers/unit-a") if worker else "",
        is_continuation=continuation,
        prior_result_status="partial" if continuation else "",
        prior_result_summary="PRIOR-RESULT-MARKER" if continuation else "",
    )


def _assert_recovery_contract(rendered: str, *, file_plan: bool) -> str:
    heading = "## Recovery loop when scope is huge"
    assert rendered.count(heading) == 1
    recovery_index = rendered.index(heading)
    request_marker = "ORIGINAL REQUEST:" if "ORIGINAL REQUEST:" in rendered else "PROMPT:"
    request_index = rendered.index(request_marker)
    plan_index = rendered.index("EXECUTION PLAN:")
    assert recovery_index < request_index < plan_index
    assert "REQUEST-FILE-MARKER.md" in rendered[request_index:plan_index]
    assert "REQUEST-CONTENT-MARKER" not in rendered
    plan_marker = "PLAN-FILE-MARKER.md" if file_plan else "PLAN-CONTENT-MARKER"
    assert plan_marker in rendered[plan_index:]
    if file_plan:
        assert "PLAN-CONTENT-MARKER" not in rendered
    else:
        assert "EXECUTION PLAN BEGIN" in rendered[plan_index:]

    early = " ".join(rendered[recovery_index:request_index].split())
    for phrase in (
        "Inventory required references",
        "acceptance criteria",
        "dependencies",
        "falsifiable increment",
        "implement",
        "focused verification",
        "evidence",
        "Reassess",
        "changed",
        "safe action remains",
        "shared/_no_exemption_for_failures.j2",
        "assessment-only",
    ):
        assert phrase in early, phrase
    normalized = " ".join(rendered.split())
    for shortcut in (
        "A blocked item with a safe concrete continuation requires",
        "report partial progress",
        "Then return the unit result.",
        "If the assignment is blocked, report",
        "submit a truthful `status: partial` result",
        "report the concrete blocker in the result instead of expanding scope",
        "follow the completion-pressure rules",
    ):
        assert shortcut not in normalized, shortcut
    for phrase in _REQUIRED_PHRASES:
        assert phrase in normalized
    return early


def _assert_worker_recovery(rendered: str, early: str, root: Path) -> None:
    for forbidden in (
        "Coordinator (not a worker)",
        "dispatch every ready independent",
        "responsible for dispatching your own sub-agents",
        "Dispatch remaining independent ready work",
    ):
        assert forbidden not in rendered
    assert "assigned unit is fully proven" in early
    assert "next ready" in early
    assert "record" in early
    assert rendered.count("## WORKER SCOPE") == 1
    assert "**Unit ID**: unit-a" in rendered
    assert "src/unit" in rendered
    assert "src/exact.py" in rendered
    normalized = " ".join(rendered.split())
    assert "Do not widen an exact-file assignment to its parent directory" in normalized
    assert "Workers never dispatch sub-agents" in normalized
    assert "unit's focused verification" in normalized
    assert "do not run it from inside this worker" in normalized
    assert "- [unit-a]" in rendered
    assert "- [S-1]" not in rendered
    namespace = root / ".agent/workers/unit-a"
    for suffix in ("artifacts/development_result.md", "handoffs/DEVELOPMENT_RESULT.md"):
        assert str(namespace / suffix) in rendered
    assert str(namespace / "tmp/development_result.md") in rendered


@pytest.mark.parametrize(
    ("template_name", "worker", "continuation"),
    (
        pytest.param("developer_iteration.jinja", False, False, id="initial"),
        pytest.param("developer_iteration_continuation.jinja", False, True, id="continuing"),
        pytest.param("developer_iteration_fallback.jinja", False, False, id="fallback"),
        pytest.param("worker_developer.jinja", True, False, id="worker"),
        pytest.param("worker_developer.jinja", True, True, id="continuing-worker"),
        pytest.param("developer_iteration_fallback.jinja", True, False, id="worker-fallback"),
        pytest.param("broken_developer.jinja", False, False, id="render-failure"),
        pytest.param("broken_developer.jinja", True, True, id="worker-render-failure"),
    ),
)
def test_rendered_recovery_precedes_scope_and_preserves_role(
    tmp_path: Path, template_name: str, worker: bool, continuation: bool
) -> None:
    context = TemplateContext.default()
    if template_name == "broken_developer.jinja":
        context.registry.register_template(template_name, "{% invalid_syntax %}")
    workspace = MemoryWorkspace(root=str(tmp_path))

    for file_plan in (False, True):
        rendered = prompt_developer_iteration_xml_with_context(
            context=context,
            inputs=_recovery_inputs(
                tmp_path, worker=worker, continuation=continuation, file_plan=file_plan
            ),
            workspace=workspace,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        )

        if template_name in ("broken_developer.jinja", "developer_iteration_fallback.jinja"):
            assert "ORIGINAL REQUEST:" in rendered
        if continuation:
            assert "PRIOR-RESULT-MARKER" in rendered
        early = _assert_recovery_contract(rendered, file_plan=file_plan)
        if worker:
            _assert_worker_recovery(rendered, early, tmp_path)
        else:
            for phrase in (
                "disjoint ownership",
                "dispatch",
                "critical path",
                "wave",
                "unavailable",
                "sequential",
                "within current authority",
            ):
                assert phrase in early, phrase


def test_recovery_guidance_references_canonical_failure_rule() -> None:
    guidance = _DEVELOPER_GUIDANCE.read_text(encoding="utf-8")
    assert "shared/_no_exemption_for_failures.j2" in guidance
    for phrase in _REQUIRED_PHRASES:
        assert phrase not in guidance
