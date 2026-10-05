"""Lock provenance-independent failure ownership in development prompts.

Also pins the large-scope contract: a development iteration whose work
proves far larger than the plan indicated is a scheduling signal, never
an exit condition. The wording is single-sourced in the
``shared/_developer_iteration_guidance.j2``, ``shared/_parallel_execution.j2``
(``.jinja`` on disk, ``.j2`` to Jinja), and
``shared/_no_exemption_for_failures.j2`` partials.
"""

from __future__ import annotations

import tempfile

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

    # S-2: the large-scope section now leads with a five-step initial
    # action sequence that applies to BOTH main and worker before
    # branching on the role. The role-specific branch must keep the
    # distinct scheduling signals that follow.
    assert "five-step initial action" in main_large_scope
    assert "five-step initial action" in worker_large_scope
    # The re-cut scheduling signal stays on the main branch.
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


def test_no_exemption_names_remaining_work_size() -> None:
    """`_no_exemption_for_failures.j2` lists the sheer size of the remaining
    work alongside the other never-justifies-partial reasons."""
    source = _PARTIAL.read_text(encoding="utf-8")
    assert "size of the remaining work never qualifies" in source


def test_no_exemption_partial_carries_parallel_dispatch_warning() -> None:
    """U-6: the partial-is-a-last-resort section warns that remaining
    independent work should be dispatched in parallel rather than
    handed back piecemeal as ``partial`` (piecemeal handbacks waste
    the cycle)."""
    source = _PARTIAL.read_text(encoding="utf-8")
    # The warning lives inside the "## Partial is a last resort" section.
    start = source.find("## Partial is a last resort")
    assert start >= 0
    # Normalize whitespace so line-wrapped phrases match the literals
    # the contract pins. The template wraps long sentences for source
    # readability, so a literal "each increment back as `partial`"
    # substring may be split across lines.
    partial_section = " ".join(source[start:].split())
    assert "dispatch it in parallel" in partial_section
    assert "piecemeal handbacks waste the cycle" in partial_section
    assert "each increment back as `partial`" in partial_section


def test_continuation_template_prior_result_block_warns_parallel_dispatch() -> None:
    """U-6: the ``Prior development result \u2014 partial`` block in
    ``developer_iteration_continuation.jinja`` carries the parallel-
    dispatch warning so a continuation session handed back a partial
    result knows the remaining independent work should be fanned out
    rather than completed piecemeal."""
    context = TemplateContext.default()
    variables = _surface_variables(
        "developer_iteration_continuation.jinja",
        is_worker=False,
        is_continuation=True,
    )
    # Populate the prior-result block so the warning is reachable.
    variables.update(
        {
            "PRIOR_RESULT_STATUS": "partial",
            "PRIOR_RESULT_SUMMARY": "Implemented step 1; steps 2-3 remain.",
            "PRIOR_RESULT_NEXT_STEPS": "Implement steps 2 and 3.",
            "PRIOR_RESULT_CONTINUATION": "agent-session-1",
        }
    )
    template = context.registry.get_template("developer_iteration_continuation.jinja")
    rendered = render_template(template, variables, context.partials)

    prior_block_start = rendered.find("Prior development result")
    assert prior_block_start >= 0, "Prior development result block missing"
    # The prior-result block ends just before the "## PARALLEL
    # EXECUTION" section. We slice the relevant range so unrelated
    # guidance text and the included ``_no_exemption_for_failures.j2``
    # partial cannot mask a regression (the canonical partial also
    # carries the same warning, but that inclusion is a separate
    # contract, not the prior-result block).
    next_heading = rendered.find("## PARALLEL EXECUTION", prior_block_start)
    prior_block_raw = (
        rendered[prior_block_start:next_heading]
        if next_heading >= 0
        else rendered[prior_block_start:]
    )
    # Normalize whitespace so line-wrapped phrases match the literals
    # the contract pins. The template wraps long sentences for source
    # readability, so a literal "dispatch it in parallel" substring
    # may be split across lines.
    prior_block = " ".join(prior_block_raw.split())
    assert "dispatch it in parallel" in prior_block
    assert "piecemeal handbacks waste the cycle" in prior_block
    assert "each increment back as `partial`" in prior_block


def test_continuation_template_parallel_dispatch_warning_absent_without_prior_result() -> None:
    """U-6: the prior-result block (and its inline parallel-dispatch
    warning) must be gated on a non-empty ``PRIOR_RESULT_STATUS``.
    A fresh continuation session with no prior result does not render
    the ``Prior development result`` block at all. The parallel-
    dispatch warning is still surfaced via the included
    ``_no_exemption_for_failures.j2`` partial, which is a separate,
    always-on contract."""
    context = TemplateContext.default()
    variables = _surface_variables(
        "developer_iteration_continuation.jinja",
        is_worker=False,
        is_continuation=True,
    )
    # PRIOR_RESULT_STATUS is left empty by `_surface_variables`; the
    # prior-result block must not render. Note the parallel-dispatch
    # warning itself is still expected because the
    # ``_no_exemption_for_failures.j2`` partial always includes it.
    template = context.registry.get_template("developer_iteration_continuation.jinja")
    rendered = render_template(template, variables, context.partials)

    assert "Prior development result" not in rendered
    assert "Continuation reference:" not in rendered


def test_rendered_developer_prompt_carries_large_scope_contract() -> None:
    """The materialized developer prompt carries the large-scope contract
    end-to-end: the main-session guidance section and the parallel-execution
    scope-size escalation both reach the rendered text."""

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
# S-1: Delivered-prompt ordering and matrix contract.
#
# Every shipped developer surface (initial, continuation, direct fallback,
# worker, worker continuation) must put the recovery mandate (the shared
# guidance partial) before the request/plan payload, once, so a
# payload-heavy plan cannot drown the persistence rules. The matrix also
# asserts that the worker fallback path keeps its no-dispatch, scope,
# focused-check, and worker-local artifact path; the main fallback must
# carry the scope-size recovery procedure; and the render-failure
# fallback path must still produce a usable prompt.
# ---------------------------------------------------------------------------


# Five rendered surfaces cover every prompt a developer session can
# receive from the bundled renderer. The matrix is the single source of
# truth for the persisted/dispatched/oversized contract: any future
# change to guidance, the parallel partial, or any one of these five
# templates must keep the assertions true for every surface. Declared
# here so the S-1 ordering tests can parameterize over them without
# depending on later module-level definitions.
_S1_SURFACE_NAMES: tuple[str, ...] = (
    "developer_iteration.jinja",  # initial main
    "developer_iteration_continuation.jinja",  # continuation main
    "developer_iteration_fallback.jinja",  # direct fallback (main)
    "worker_developer.jinja",  # dedicated worker (initial)
    "worker_developer.jinja#continuation",  # dedicated worker (continuation)
)


_RECOVERY_MARKERS = (
    "## Completion is the default outcome",
    "## Large scope is a dispatch signal",
    "## Scope-size recovery procedure",
)
# Recognizable request/plan payload markers. The templates use
# ``render_payload_section`` so the rendered text wraps each section in a
# recognisable heading like ``## PROMPT`` (initial, worker) or
# ``## ORIGINAL REQUEST`` (fallback). The plan payload is rendered as
# ``## EXECUTION PLAN``. Any of these counts as a payload marker.
_PAYLOAD_MARKERS = (
    "PROMPT:",
    "ORIGINAL REQUEST:",
    "EXECUTION PLAN:",
)


def _first_marker(text: str, markers: tuple[str, ...]) -> int:
    positions = [text.find(m) for m in markers if text.find(m) >= 0]
    return min(positions) if positions else -1


def _guidance_appears_once(text: str) -> int:
    """Count the distinct occurrences of the recovery mandate heading."""
    return text.count("## Completion is the default outcome")


@pytest.mark.parametrize("surface", _S1_SURFACE_NAMES, ids=_S1_SURFACE_NAMES)
def test_s1_recovery_mandate_precedes_payload_markers(surface: str) -> None:
    """S-1: the recovery mandate must reach the rendered prompt BEFORE
    the request/plan payload markers on every shipped developer
    surface. Putting the persistence rules after a large payload lets
    a heavy plan bury them; the agent must see the persistence
    language first.
    """
    rendered = _render_surface(surface)

    guidance_pos = _first_marker(rendered, _RECOVERY_MARKERS)
    payload_pos = _first_marker(rendered, _PAYLOAD_MARKERS)
    assert guidance_pos >= 0, (
        f"{surface}: rendered prompt is missing the recovery mandate "
        "(Completion / Large scope / Scope-size recovery)"
    )
    assert payload_pos >= 0, (
        f"{surface}: rendered prompt is missing a recognizable payload marker"
    )
    assert guidance_pos < payload_pos, (
        f"{surface}: recovery mandate must precede the request/plan payload "
        f"(guidance at {guidance_pos}, payload at {payload_pos})"
    )


@pytest.mark.parametrize("surface", _S1_SURFACE_NAMES, ids=_S1_SURFACE_NAMES)
def test_s1_recovery_mandate_occurs_exactly_once(surface: str) -> None:
    """S-1: every shipped developer surface includes the recovery
    mandate exactly once. Duplication would mean the templates include
    the guidance partial twice; absence would mean a future edit
    silently dropped the contract.
    """
    rendered = _render_surface(surface)
    count = _guidance_appears_once(rendered)
    assert count == 1, (
        f"{surface}: recovery mandate must occur exactly once, got {count}"
    )


def test_s1_main_fallback_carries_scope_size_recovery_procedure() -> None:
    """S-1: the main-session direct fallback must carry the
    scope-size recovery procedure. A render failure that drops the
    developer onto the fallback must still show the main-session
    dispatch, integrate, and full-gate checklist; the worker branch
    must stay scoped to the assigned unit.
    """
    context = TemplateContext.default()
    variables = _surface_variables(
        "developer_iteration_fallback.jinja",
        is_worker=False,
        is_continuation=False,
    )
    template = context.registry.get_template("developer_iteration_fallback.jinja")
    rendered = render_template(template, variables, context.partials)

    for phrase in (
        "Scope-size recovery procedure",
        "Inventory the remaining references",
        "Dispatch within the available native capacity",
        "Work the critical path",
        "Continue owned ready work",
        "Integrate and verify",
    ):
        assert phrase in rendered, (
            f"main fallback is missing scope-size recovery phrase {phrase!r}"
        )
    # The worker-only directives must NOT appear on the main fallback.
    for phrase in (
        "Workers never dispatch sub-agents",
        "WORKER-SCOPED VERIFICATION",
    ):
        assert phrase not in rendered, (
            f"main fallback must not carry worker-only phrase {phrase!r}"
        )


def test_s1_worker_fallback_retains_no_dispatch_scope_focus_and_local_paths() -> None:
    """S-1: the worker-fallback surface (``developer_iteration_fallback.jinja``
    with ``IS_WORKER=1``) must keep the worker contract: no recursive
    dispatch, the assigned unit's allowed directories, the focused
    ``Verify`` command, and the worker-local artifact path. Without
    these, a render-failure fallback would let a worker broaden scope
    into another unit's namespace or run the full repository gate.
    """
    context = TemplateContext.default()
    variables = _surface_variables(
        "developer_iteration_fallback.jinja",
        is_worker=True,
        is_continuation=False,
    )
    template = context.registry.get_template("developer_iteration_fallback.jinja")
    rendered = render_template(template, variables, context.partials)

    # No recursive dispatch.
    assert "Workers never dispatch sub-agents" in rendered
    assert "WORKER DO-NOT-DISPATCH" in rendered
    # Assigned unit scope is named.
    assert "WORKER SCOPE" in rendered
    assert "Allowed directories" in rendered
    # Focused (not full-gate) verification.
    assert "WORKER-SCOPED VERIFICATION" in rendered
    # Worker-local artifact path.
    assert ".agent/workers/api/artifacts/development_result.md" in rendered
    # The main-session full gate must NOT appear in the worker branch.
    assert "full `make verify`" not in rendered, (
        "worker fallback must not tell the worker to run the full "
        "repository-wide gate"
    )


def test_s1_worker_fallback_replaces_loose_blocked_sentence_with_canonical_rule() -> None:
    """S-1: the worker's loose "If the unit remains blocked, submit a
    truthful ``status: partial``" sentence weakens the canonical
    external-blocker rule (it would let any unfocused difficulty end
    the unit). The worker fallback must defer to the canonical rule
    instead and keep the loop-until-verified contract.
    """
    context = TemplateContext.default()
    variables = _surface_variables(
        "developer_iteration_fallback.jinja",
        is_worker=True,
        is_continuation=False,
    )
    template = context.registry.get_template("developer_iteration_fallback.jinja")
    rendered = render_template(template, variables, context.partials)

    # The loose, unconditional "submit a truthful `status: partial`"
    # must be gone; the canonical external-blocker rule is the only
    # partial/failed source.
    assert "submit a truthful `status: partial`" not in rendered, (
        "worker fallback must not carry a loose unconditional partial-result sentence"
    )
    # The canonical rule must still be present (sourced from
    # ``_no_exemption_for_failures.j2``).
    assert "no such thing as a pre-existing issue" in rendered
    # The loop-until-verified wording must still reach the worker.
    assert "until the entire assigned unit is verified" in rendered


def test_s1_worker_developer_replaces_loose_blocked_sentence_with_canonical_rule() -> None:
    """S-1 (sibling surface): the dedicated ``worker_developer.jinja``
    template also carries the loose "If the assignment is blocked,
    report the concrete blocker" sentence. It must defer to the
    canonical external-blocker rule, not grant a free partial path
    for any assignment-side difficulty.
    """
    context = TemplateContext.default()
    variables = _surface_variables(
        "worker_developer.jinja", is_worker=True, is_continuation=False
    )
    template = context.registry.get_template("worker_developer.jinja")
    rendered = render_template(template, variables, context.partials)

    assert "If the assignment is blocked, report" not in rendered, (
        "worker_developer.jinja must not carry a loose unconditional "
        "blocked-result sentence"
    )
    # Canonical rule still sourced from the shared partial.
    assert "no such thing as a pre-existing issue" in rendered
    # Loop-until-verified contract still reaches the dedicated worker.
    assert "until the entire assigned unit is verified" in rendered


def test_s1_render_failure_fallback_still_carries_persistence_contract() -> None:
    """S-1: when the public developer renderer hits a template
    rendering error, it must hand the agent a usable prompt that
    still carries the canonical external-blocker rule, the
    scope-size recovery procedure, and the no-recursive-dispatch
    worker language. A render failure cannot drop the persistence
    contract.
    """
    from ralph.prompts.developer import (
        DeveloperPromptInputs,
        prompt_developer_iteration_xml_with_context,
    )
    from ralph.prompts.types import SessionCapabilities, SessionDrain
    from ralph.workspace.memory import MemoryWorkspace

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        workspace = MemoryWorkspace(root=str(tmp_path))
        # A template that renders Jinja but contains an undefined
        # variable: Jinja raises ``UndefinedError`` which the renderer
        # turns into ``TemplateRenderingError`` and falls back to the
        # static fallback template.
        prompt = prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement it", plan_content="### [S-1] Change it"
            ),
            workspace=workspace,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name="developer_iteration.jinja",
        )
        # Force a render failure by overriding the template to one that
        # references a missing variable. Use a tiny inline template
        # via the public fallback path: ``_render_static_fallback``
        # is reached whenever ``render_template`` raises
        # ``TemplateRenderingError``. We exercise that path by calling
        # the developer renderer with a context whose template raises.
        class _BrokenTemplate:
            def render(self, *args, **kwargs):  # noqa: D401 - test seam
                from ralph.prompts.template_rendering_error import (
                    TemplateRenderingError,
                )

                raise TemplateRenderingError("forced render failure for test")

        broken_registry = TemplateContext.default().registry
        original_get = broken_registry.get_template

        def _force_broken(name):
            if name == "developer_iteration.jinja":
                return _BrokenTemplate()
            return original_get(name)

        broken_registry.get_template = _force_broken  # type: ignore[assignment]
        try:
            prompt = prompt_developer_iteration_xml_with_context(
                context=TemplateContext.default(),
                inputs=DeveloperPromptInputs(
                    prompt_content="Implement it",
                    plan_content="### [S-1] Change it",
                ),
                workspace=workspace,
                session_caps=SessionCapabilities.defaults_for_drain(
                    SessionDrain.DEVELOPMENT
                ),
            )
        finally:
            broken_registry.get_template = original_get  # type: ignore[assignment]

    for phrase in (
        "## Completion is the default outcome",
        "## Scope-size recovery procedure",
        "no such thing as a pre-existing issue",
        "MUST resolve anything that comes up",
    ):
        assert phrase in prompt, (
            f"render-failure fallback is missing the persistence contract "
            f"phrase {phrase!r}"
        )


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


# ---------------------------------------------------------------------------
# Worker-isolation in the recovery procedure.
#
# The worker prompt must not direct the worker to dispatch other
# sub-agents or run the full ``make verify`` gate. The shared guidance
# partial carries one scope-size recovery section that branches on
# ``IS_WORKER``: the main-session branch keeps the dispatch + integrate
# + full-gate checklist, the worker branch shrinks the unit into
# smaller verified increments and loops within it. These tests pin
# the rendered worker surfaces so a regression cannot silently
# reintroduce the contradictory dispatch/full-gate directives.
# ---------------------------------------------------------------------------


_WORKER_SURFACE_NAMES: tuple[str, ...] = (
    "worker_developer.jinja",
    "worker_developer.jinja#continuation",
)

# Phrases that belong only to the main-session recovery checklist.
# A worker render must NOT carry any of them in the recovery
# procedure section, or the worker will be instructed to dispatch
# sub-agents and run the full repository-wide gate.
_MAIN_ONLY_RECOVERY_PHRASES: tuple[str, ...] = (
    "Dispatch within the available native capacity",
    "Give each writer exact paths and proof obligations",
    "Work the critical path while helpers run",
    "Continue owned ready work when slots are saturated",
)


def _recovery_section(text: str) -> str:
    """Return the scope-size recovery procedure section (or empty when absent).

    The shared guidance partial uses the heading
    ``## Scope-size recovery procedure`` and ends the section before
    ``## Plan fidelity``. Tests scope their assertions to that
    section so unrelated worker-only or main-only paragraphs cannot
    mask a regression.
    """
    start = text.find("## Scope-size recovery procedure")
    if start < 0:
        return ""
    end = text.find("## Plan fidelity", start)
    if end < 0:
        return text[start:]
    return text[start:end]


@pytest.mark.parametrize("surface", _WORKER_SURFACE_NAMES, ids=_WORKER_SURFACE_NAMES)
def test_worker_renders_reject_dispatch_directives_in_recovery_procedure(
    surface: str,
) -> None:
    """Worker renders must not carry the main-session dispatch checklist.

    The unconditional checklist told workers to dispatch other
    sub-agents ("Dispatch within the available native capacity",
    "Give each writer exact paths", "Work the critical path while
    helpers run", "Continue owned ready work when slots are
    saturated") and run the full ``make verify`` gate — instructions
    that directly contradict the WORKER DO-NOT-DISPATCH and
    WORKER-SCOPED VERIFICATION contracts in
    ``shared/_worker_verification.jinja``. Asserting each phrase is
    absent from the recovery section pinpoints the regression in a
    single line.
    """
    rendered = _render_surface(surface)
    recovery = _recovery_section(rendered)

    assert recovery, f"{surface}: missing the recovery-procedure section"
    for phrase in _MAIN_ONLY_RECOVERY_PHRASES:
        assert phrase not in recovery, (
            f"{surface}: worker recovery procedure must not include {phrase!r}; "
            "this directive tells workers to dispatch sub-agents"
        )


@pytest.mark.parametrize("surface", _WORKER_SURFACE_NAMES, ids=_WORKER_SURFACE_NAMES)
def test_worker_renders_reject_full_make_verify_in_recovery_procedure(
    surface: str,
) -> None:
    """Worker renders must not direct the worker to run ``make verify``.

    The full repository-wide gate runs in the main session, not in
    any worker. A recovery checklist that tells the worker to "run
    the full ``make verify`` run" leaks main-session responsibility
    into worker scope and contradicts WORKER-SCOPED VERIFICATION.
    """
    rendered = _render_surface(surface)
    recovery = _recovery_section(rendered)

    assert recovery, f"{surface}: missing the recovery-procedure section"
    assert "full `make verify`" not in recovery, (
        f"{surface}: worker recovery procedure must not require running "
        "the full `make verify` gate; that runs in the main session"
    )
    assert "Integrate and verify." not in recovery, (
        f"{surface}: worker recovery procedure must not include the "
        "Integrate and verify step that runs the repository-wide gate"
    )


@pytest.mark.parametrize("surface", _WORKER_SURFACE_NAMES, ids=_WORKER_SURFACE_NAMES)
def test_worker_renders_carry_worker_safe_recovery_procedure(surface: str) -> None:
    """Worker renders must keep a recovery procedure scoped to the
    assigned unit: shrink into verified increments, loop until the
    unit is fully proven, never dispatch sub-agents, never run the
    full gate. The wording is single-sourced in
    ``shared/_developer_iteration_guidance.j2``'s ``IS_WORKER`` branch.
    """
    rendered = _render_surface(surface)
    recovery = _recovery_section(rendered)

    assert recovery, f"{surface}: missing the recovery-procedure section"
    # Worker-safe recovery language that must remain in the rendered
    # worker prompt so the worker keeps looping within its assigned
    # unit instead of returning after the first increment.
    for phrase in (
        "Scope-size recovery procedure",
        "verified increments",
        "fully proven",
        "shared/_no_exemption_for_failures.j2",
    ):
        assert phrase in recovery, f"{surface}: missing worker-safe phrase {phrase!r}"
    # The worker-only contracts from the dedicated worker partial
    # must still be present on every worker surface.
    assert "WORKER DO-NOT-DISPATCH" in rendered, surface
    assert "Workers never dispatch sub-agents" in rendered, surface
    assert "WORKER-SCOPED VERIFICATION" in rendered, surface
    assert "until the entire assigned unit is verified" in rendered, surface
