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

import pytest
from jinja2 import Environment

from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, SessionDrain, capability_template_variables

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
    and dispatches concurrently. The recovery-procedure section that
    follows legitimately applies to both branches, so the assertions here
    are scoped to the large-scope section only via the heading markers.
    """
    guidance = TemplateContext.default().registry.get_template(
        "shared/_developer_iteration_guidance"
    )
    template = Environment().from_string(guidance)
    main = template.render(IS_WORKER=False)
    worker = template.render(IS_WORKER=True)

    main_large_scope = _extract_section(
        main, "## Large scope is a dispatch signal", "## Scope-size recovery procedure"
    )
    worker_large_scope = _extract_section(
        worker, "## Large scope is a dispatch signal", "## Scope-size recovery procedure"
    )

    assert "re-cut the remainder" in main_large_scope
    assert "re-cut the remainder" not in worker_large_scope
    assert "verified increments" in worker_large_scope
    # Distinct main/worker markers survive in the large-scope section.
    assert "execution defect" in main_large_scope
    assert "Magnitude alone" in worker_large_scope


def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between ``start_marker`` and ``end_marker`` (or the
    rest of the document when ``end_marker`` is absent). Used to scope
    section-specific assertions in tests that render an entire
    guidance template.
    """
    start = text.find(start_marker)
    if start < 0:
        return ""
    after_start = start + len(start_marker)
    end = text.find(end_marker, after_start)
    if end < 0:
        return text[after_start:]
    return text[after_start:end]


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


# ---------------------------------------------------------------------------
# Persisted, dispatched, and oversized-work recovery contract.
#
# The previous text of the shared guidance allowed two escape clauses
# that contradicted the canonical external-blocker rule: an unconditional
# "or report partial progress" after a failed approach, and a worker
# branch that told the worker to return the unit result after the first
# ready reference. This matrix locks the rendered behaviour for every
# shipped developer surface (initial, continuation, direct fallback,
# worker, worker continuation) so future drift cannot reintroduce the
# contradiction.
# ---------------------------------------------------------------------------


def _surface_variables(
    template_name: str,
    *,
    is_worker: bool,
    is_continuation: bool,
) -> dict[str, str]:
    """Build the render-time variable set the developer renderer would supply."""
    session = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    base: dict[str, str] = {
        **capability_template_variables(session.capabilities, session.policy_flags),
        "PRODUCT_CRITERIA": "Preserve the requested behavior.",
        "PRODUCT_CRITERIA_PATH": "PROMPT.md",
        "PLAN": "### [S-1] Implement the assigned change",
        "PLAN_PATH": ".agent/PLAN.md",
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": "",
        "ANALYSIS_FEEDBACK_STATUS": "",
        "PRIOR_RESULT_STATUS": "",
        "PRIOR_RESULT_SUMMARY": "",
        "PRIOR_RESULT_NEXT_STEPS": "",
        "PRIOR_RESULT_CONTINUATION": "",
        "LAST_RETRY_ERROR": "",
        "HAS_GIT_WRITE": "",
        "HAS_DOCS_MCP": "",
        "DOCS_MCP_PORT": "localhost:6280",
        "ARTIFACT_HISTORY_PATH": "",
        "ARTIFACT_HISTORY_DIR": "",
        "SKILLS_INLINE_CONTENT": "",
        "SKILLS_MANIFEST_PATH": "",
        "VERIFICATION_COMMAND": "make verify",
        "RUN_BUDGET_SECONDS": "60",
        "EXEC_TOOL_NAME": "ralph_exec",
        "WRITE_FILE_TOOL_REFERENCE": "ralph_write_file",
        "REPORT_PROGRESS_TOOL_NAME": "ralph_report_progress",
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE": "ralph_submit_md_artifact",
        "DECLARE_COMPLETE_TOOL_REFERENCE": "declare_complete",
        "IS_CONTINUATION": "1" if is_continuation else "",
        "IS_WORKER": "1" if is_worker else "",
        "unit_id": "api",
        "description": "Implement API",
        "allowed_directories": "src/api",
        "WORKER_NAMESPACE": ".agent/workers/api",
        "WORKER_FALLBACK_PATH": ".agent/workers/api/tmp/development_result.md",
    }
    if template_name == "developer_iteration_fallback.jinja" and not is_worker:
        # The main-session fallback only renders the worker scope block
        # for IS_WORKER=1; the WORKER_* fields stay empty in the main
        # path so we don't accidentally inject worker-only language.
        base.update(
            {
                "unit_id": "",
                "description": "",
                "allowed_directories": "",
                "WORKER_NAMESPACE": "",
                "WORKER_FALLBACK_PATH": "",
            }
        )
    if template_name == "developer_iteration_continuation.jinja" and not is_continuation:
        # The continuation template's worker contract still requires
        # the worker-only fields; the non-continuation renderer does
        # not.
        pass
    if template_name in ("developer_iteration.jinja",) and is_continuation:
        # The non-continuation renderer does not read IS_CONTINUATION
        # itself; the dispatcher picks ``developer_iteration_continuation.jinja``
        # instead, so the matrix stays on the literal template that
        # ships in the prompt directory.
        pass
    return base


# Five rendered surfaces cover every prompt a developer session can
# receive from the bundled renderer. The matrix is the single source of
# truth for the persisted/dispatched/oversized contract: any future
# change to guidance, the parallel partial, or any one of these five
# templates must keep the assertions true for every surface.
_SURFACE_NAMES: tuple[str, ...] = (
    "developer_iteration.jinja",  # initial main
    "developer_iteration_continuation.jinja",  # continuation main
    "developer_iteration_fallback.jinja",  # direct fallback (main)
    "worker_developer.jinja",  # dedicated worker (initial)
    "worker_developer.jinja#continuation",  # dedicated worker (continuation)
)


def _render_surface(name: str) -> str:
    """Render ``name`` with the variables the developer renderer would supply."""
    context = TemplateContext.default()
    if name == "worker_developer.jinja#continuation":
        variables = _surface_variables(
            "worker_developer.jinja", is_worker=True, is_continuation=True
        )
    else:
        is_worker = name in {"worker_developer.jinja", "worker_developer.jinja#continuation"}
        is_continuation = name == "developer_iteration_continuation.jinja"
        if name == "developer_iteration_fallback.jinja":
            variables = _surface_variables(
                "developer_iteration_fallback.jinja",
                is_worker=False,
                is_continuation=False,
            )
        else:
            variables = _surface_variables(
                name, is_worker=is_worker, is_continuation=is_continuation
            )
    template = context.registry.get_template(name.split("#", 1)[0])
    return render_template(template, variables, context.partials)


@pytest.mark.parametrize("surface", _SURFACE_NAMES, ids=_SURFACE_NAMES)
def test_rendered_prompts_reject_partial_progress_escape_clause(surface: str) -> None:
    """A failed approach does not open a free "report partial progress"
    door. The rendered guidance must defer to the canonical
    external-blocker rule in ``_no_exemption_for_failures.j2`` for any
    terminal partial/failed decision, on every shipped developer
    surface. Asserting the literal escape-clause phrase is absent
    pinpoints the historic regression in one line.
    """
    rendered = _render_surface(surface)

    assert "or report partial progress" not in rendered, surface
    # The rendered guidance must point at the canonical exemption rule,
    # not the old escape clause.
    assert "shared/_no_exemption_for_failures.j2" in rendered, surface
    # The canonical rule itself must be present in every surface.
    assert "no such thing as a pre-existing issue" in rendered, surface


@pytest.mark.parametrize("surface", _SURFACE_NAMES, ids=_SURFACE_NAMES)
def test_rendered_prompts_reject_first_increment_worker_return(surface: str) -> None:
    """The worker branch must not instruct the worker to return the
    unit result after the first ready reference. Workers loop the
    ready-reference walk until the entire assigned unit is verified or
    an evidence-backed external blocker is recorded.
    """
    rendered = _render_surface(surface)

    assert "Then return the unit result" not in rendered, surface
    # The worker-only loop-until-verified contract is in the
    # ``IS_WORKER`` branch of the shared guidance. Main-session renders
    # (initial, continuation, direct fallback) intentionally use a
    # different "every required plan reference" phrasing because they
    # are not looping a single worker's unit; the worker prompt
    # surfaces must carry the loop-until-verified phrase.
    if surface in {"worker_developer.jinja", "worker_developer.jinja#continuation"}:
        assert "until the entire assigned unit is verified" in rendered, surface
        assert "Workers never dispatch sub-agents" in rendered, surface
        assert "WORKER DO-NOT-DISPATCH" in rendered, surface


@pytest.mark.parametrize(
    "surface",
    ("developer_iteration.jinja", "developer_iteration_continuation.jinja"),
    ids=("initial", "continuation"),
)
def test_rendered_main_prompts_carry_scope_growth_recovery_procedure(
    surface: str,
) -> None:
    """Main-session renders carry the concrete scope-growth procedure:
    inventory remaining references, dispatch within available native
    capacity, keep owned ready work running when slots are saturated,
    integrate, and verify. The previous text pointed at the worker
    brief once and stopped; this pins the new recovery checklist.
    """
    rendered = _render_surface(surface)

    for phrase in (
        "Scope-size recovery procedure",
        "Inventory the remaining references",
        "Dispatch within the available native capacity",
        "Work the critical path",
        "Continue owned ready work",
        "slots are saturated",
        "Integrate and verify",
    ):
        assert phrase in rendered, f"{surface}: missing {phrase!r}"


@pytest.mark.parametrize("surface", _SURFACE_NAMES, ids=_SURFACE_NAMES)
def test_rendered_prompts_preserve_outer_constraints(surface: str) -> None:
    """The strength of the rendered guidance comes from the contracts
    that must remain true: the canonical external-blocker rule, no
    recursive worker dispatch, focused worker verification, the
    main-session final verification gate, and the immutable 60-second
    test budget. These pins guard against wording edits that would
    weaken the surface-level rules the contract depends on.
    """
    rendered = _render_surface(surface)

    # Canonical external-blocker rule is single-sourced.
    assert "no such thing as a pre-existing issue" in rendered, surface
    assert "MUST resolve anything that comes up" in rendered, surface
    # No recursive worker dispatch lives on the dedicated worker
    # template's ``_worker_verification.jinja`` partial. The main-session
    # surfaces (initial, continuation, direct fallback) never render
    # that partial, so the contract is checked against the worker
    # surfaces below.
    if surface in {"worker_developer.jinja", "worker_developer.jinja#continuation"}:
        assert "Workers never dispatch sub-agents" in rendered, surface
        assert "WORKER-SCOPED VERIFICATION" in rendered, surface
    # The main-session final verification gate stays on the main
    # surfaces; the direct fallback also keeps it.
    if surface in {
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    }:
        assert "full gate before completion" in rendered, surface
    # The run-budget partial's primary contract is "use the full run
    # budget to advance the plan" - that phrase is the only stable
    # signal that the partial was included, and it ships on every
    # developer surface (main and worker alike).
    assert "Use the full run budget" in rendered, surface

