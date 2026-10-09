"""Rendered parallel-execution guidance asserts coordinator dispatch contracts.

The shared partials must produce role-aware prose on the public rendering
surface (``prompt_developer_iteration_xml_with_context`` with a
``MemoryWorkspace``). Source-text inspection is intentionally avoided:
rendered-output assertions catch template regressions that source grep
misses, and they survive partial rewording.

Coordinator renderings (regular, continuation, fallback) must surface
  the parallel-by-default anchors AND keep the existing four shared
  contract behaviors (independent ready steps, queue/local progress,
  exposed-tool discipline, stopped-writer transfer).

The feedback-path contract (Unit 1 S-2/S-3) adds a second dimension:
* Coordinator renders with ``ANALYSIS_FEEDBACK_STATUS=request_changes``
  must surface the mandatory follow-plan sentence (MUST follow division,
  dispatch every ready unit in parallel) and the explicit-plan-reason
  anchor. The unconditional "the analysis above splits" claim and the
  optional "may split ... When it does" framing must never appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.prompts._capability_set import CapabilitySet
from ralph.prompts._policy_flag import PolicyFlag
from ralph.prompts._policy_flag_set import PolicyFlagSet
from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# Coordinator-only behaviors the S-1/U-1 work must make visible on every
# coordinator rendering. These are the four pre-existing contract anchors
# (independent ready steps, queue/local progress, exposed-tool discipline,
# stopped-writer transfer) plus the parallel-by-default mandatory wording.
_COORDINATOR_INDEPENDENT_READY = "independent ready"
_COORDINATOR_QUEUE_OR_LOCAL = "queue"
_COORDINATOR_EXPOSED_TOOLS = "exposed"
_COORDINATOR_STOPPED_WRITER = "stopped"

# Coordinator-only pinned anchors (exact literals). These must surface on
# every coordinator rendering and must be ABSENT from every worker
# rendering. The shared wording contract is the single source of truth
# for the parallel-by-default rewrite.
_COORDINATOR_PINS_REQUIRED: tuple[str, ...] = (
    "Parallel execution of independent ready units is required by default",
    "dispatch every ready unit concurrently",
    "Sequential execution requires an explicit plan reason",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text forced it",
    "## PARALLEL EXECUTION (required by default)",
)

# Negative rendered-output anchors: no coordinator rendering may emit any
# of the removed fallback phrases, regardless of capability input. The
# source-level grep check in S-1 alone is insufficient because include /
# render paths could reintroduce text from elsewhere; the rendered-output
# assertion closes that gap.
_COORDINATOR_RENDERED_NEGATIVES: tuple[str, ...] = (
    "missing sub-agent tool",
    "bounded sequential",
    "bounded-sequential",
    "fall back to bounded sequential",
    "no native sub-agent",
    "runtime limit",
)

# Negative source guards: the shared partial must not contain the
# removed fallback phrases. Source-level guards protect future partial
# rewrites from re-introducing capability-gated sequential fallbacks.
_PARTIAL_SOURCE_NEGATIVES: tuple[str, ...] = (
    "or a missing sub-agent tool",
    "bounded-sequential",
    "no native sub-agent / task tool is exposed",
    "runtime limit",
)

# Distinct ownership/waves protections: the shared parallel-execution
# partial must keep the exact-path ownership, waves, and protected-path
# sanitization language on the coordinator rendering.
_PROTECTED_ROOTS = (".agent", ".git", ".worktrees")
_OWNERSHIP_FIELDS = ("Paths:", "Directories:", "Files:")


def _render(template_name: str, tmp_path: Path) -> str:
    """Render one development prompt surface to a whitespace-normalized form."""
    return " ".join(
        prompt_developer_iteration_xml_with_context(
            context=TemplateContext.default(),
            inputs=DeveloperPromptInputs(
                prompt_content="Implement the requested change.",
                plan_content="### [S-1] Implement the assigned change",
            ),
            workspace=MemoryWorkspace(root=str(tmp_path)),
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            template_name=template_name,
        ).split()
    )


def _render_raw_with_caps(
    template_name: str,
    tmp_path: Path,
    *,
    session_caps: SessionCapabilities,
) -> str:
    """Render one development prompt surface verbatim, with newlines preserved.

    The capability-input invariance test needs to extract a section that
    ends at a ``\\n## `` boundary; whitespace normalization would erase
    that boundary, so this variant returns the raw rendered prompt.
    """
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="### [S-1] Implement the assigned change",
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=session_caps,
        template_name=template_name,
    )


def _render_with_feedback(
    template_name: str,
    tmp_path: Path,
    *,
    analysis_feedback_status: str,
    analysis_feedback_content: str,
) -> str:
    """Render one development prompt with analysis-feedback inputs.

    The Unit-2 feedback-path tests need a render that exercises the
    ``ANALYSIS_FEEDBACK`` / ``ANALYSIS_FEEDBACK_STATUS`` template
    branches. The base ``_render`` helper hard-codes empty feedback,
    which means the S-2/S-3 follow-plan sentence is never rendered.
    A dedicated helper keeps the rest of the suite's behavior
    untouched.
    """
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement the requested change.",
            plan_content="### [S-1] Implement the assigned change",
            analysis_feedback_status=analysis_feedback_status,
            analysis_feedback_content=analysis_feedback_content,
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )


def _extract_parallel_section(rendered: str) -> str:
    """Return the parallel orchestration section from a full coordinator render.

    The boundary starts at ``## PARALLEL EXECUTION`` and extends through
    the next section boundary (``\\n## ``) or, for the fallback template,
    the start of the subsequent capability-dependent tool-name guidance
    (``Use runtime-native orchestration``).

    This captures the full parallel orchestration section including
    template-specific framing (such as the continuation template's
    mandatory continuation-dispatch preamble and the fallback template's
    coverage-check blurb) while excluding unrelated capability-dependent
    tool-name prose.
    """
    start = rendered.find("## PARALLEL EXECUTION")
    if start < 0:
        return ""
    tool_guidance = "Use runtime-native orchestration"
    tool_boundary = rendered.find(tool_guidance, start)
    next_heading = rendered.find("\n## ", start + 1)

    candidates = [pos for pos in (next_heading, tool_boundary) if pos > 0]
    if not candidates:
        return rendered[start:]
    return rendered[start : min(candidates)]


@pytest.mark.parametrize(
    "template_name",
    (
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "developer_iteration_fallback.jinja",
    ),
)
def test_rendered_parallel_execution_contracts_match_role(
    tmp_path: Path, template_name: str
) -> None:
    """S-1/S-2: every surface renders role-aware parallel-execution prose.

    Coordinator renderings must surface the four shared-contract behaviors
    (independent ready steps, queue/local progress, exposed-tool discipline,
    stopped-writer transfer) AND every coordinator-only pinned anchor from
    the parallel-by-default rewrite.
    """
    rendered = _render(template_name, tmp_path)
    rendered_lower = rendered.lower()
    assert _COORDINATOR_INDEPENDENT_READY in rendered_lower
    assert _COORDINATOR_QUEUE_OR_LOCAL in rendered_lower
    assert _COORDINATOR_EXPOSED_TOOLS in rendered_lower
    assert _COORDINATOR_STOPPED_WRITER in rendered_lower
    for token in _PROTECTED_ROOTS:
        assert token in rendered, f"protected root {token!r} missing in {template_name}"
    for token in _OWNERSHIP_FIELDS:
        assert token in rendered, f"ownership token {token!r} missing in {template_name}"
    missing = [pin for pin in _COORDINATOR_PINS_REQUIRED if pin not in rendered]
    assert not missing, (
        f"coordinator rendering {template_name!r} missing pinned "
        f"parallel-by-default anchors: {missing!r}"
    )


def test_shared_parallel_partial_keeps_sanitization_and_waves() -> None:
    """The shared partial keeps protected-path sanitization and waves.

    Source-text guard for the partial itself: the partial is the only
    author of those protections, and a future partial rewrite must keep
    the protected-path and ownership tokens verbatim.
    """
    partial = (
        Path(__file__).resolve().parents[1]
        / "ralph"
        / "prompts"
        / "templates"
        / "shared"
        / "_parallel_execution.jinja"
    )
    source = partial.read_text(encoding="utf-8")

    for token in _PROTECTED_ROOTS:
        assert token in source, f"protected root {token!r} missing in parallel partial"
    for token in _OWNERSHIP_FIELDS:
        assert token in source, f"ownership token {token!r} missing in parallel partial"
    assert "waves" in source
    assert "Serialize conflicting ownership" in source
    assert "Do not broaden file ownership" in source
    # Parallel-by-default rewrite: the partial must open with the new
    # mandatory wording, must state the explicit plan reason, and must
    # keep the large-plan-cue phrasing.
    assert "Parallel execution of independent ready units is required by default" in source
    assert "Sequential execution requires an explicit plan reason" in source
    assert "not a reason to stop, hand back, split the task, or return `partial`" in source
    # Queue-in-waves split: a full cap queues remaining ready units, the
    # ownership-unresolvable and malformed-graph safeguards survive.
    assert "queue the remaining ready units" in source
    assert "ownership unresolvable" in source
    assert "malformed" in source
    # Negative source guards: the removed fallback phrases must not
    # survive in the partial. These are the S-1 step 5 / S-2 step 1
    # negative pins.
    for forbidden in _PARTIAL_SOURCE_NEGATIVES:
        assert forbidden not in source, (
            f"parallel partial re-introduced removed fallback phrase {forbidden!r}"
        )


# Coordinator templates that must satisfy the capability-input
# invariance and negative rendered fallback assertions. Worker-only
# templates intentionally do not appear here — the contract is
# "no non-sub-agent fallback wording reaches a worker", which the
# role-aware test already covers.
_COORDINATOR_TEMPLATES: tuple[str, ...] = (
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
)


def _maximally_different_caps() -> SessionCapabilities:
    """Return a SessionCapabilities deliberately opposed to the defaults.

    Drops the default shell + write capabilities, adds a non-default
    tool-name prefix, and flips one policy flag. The point is not the
    specific values but that the parallel-execution section is
    byte-identical under maximally different inputs: no capability
    input reaches the parallel wording.
    """
    return SessionCapabilities(
        capabilities=CapabilitySet(
            (
                Capability.WORKSPACE_READ,
                Capability.ARTIFACT_SUBMIT,
                Capability.ARTIFACT_PLAN_READ,
                Capability.RUN_REPORT_PROGRESS,
                Capability.GIT_STATUS_READ,
            )
        ),
        policy_flags=PolicyFlagSet((PolicyFlag.ALLOW_NETWORK, PolicyFlag.ALLOW_ENV_READ)),
        tool_name_prefix="alternate_runtime_",
    )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_parallel_section_is_capability_invariant(
    tmp_path: Path, template_name: str
) -> None:
    """The parallel-execution section must not depend on capability inputs.

    Renders the same coordinator template under
    ``SessionCapabilities.defaults_for_drain(DEVELOPMENT)`` and a
    maximally different ``SessionCapabilities``, then extracts the
    ``## PARALLEL EXECUTION`` section from each full render and asserts
    the two are byte-identical. The same section must also contain the
    parallel-by-default mandatory wording and must NOT contain any of
    the removed fallback phrases under either capability input.

    A partial-only render of ``shared/_parallel_execution.jinja`` is
    not used: capability-sensitive variables can affect coordinator
    includes and framing outside the partial, and only a full-prompt
    section comparison proves no capability input reaches the parallel
    wording.
    """
    default_caps = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    alternate_caps = _maximally_different_caps()

    default_rendered = _render_raw_with_caps(
        template_name, tmp_path, session_caps=default_caps
    )
    alternate_rendered = _render_raw_with_caps(
        template_name, tmp_path, session_caps=alternate_caps
    )

    default_section = _extract_parallel_section(default_rendered)
    alternate_section = _extract_parallel_section(alternate_rendered)

    assert default_section, f"parallel section missing from {template_name!r} default render"
    assert alternate_section, f"parallel section missing from {template_name!r} alternate render"
    assert default_section == alternate_section, (
        f"parallel section in {template_name!r} changed under alternate "
        f"capability inputs; capability gating is forbidden"
    )

    if template_name == "developer_iteration_continuation.jinja":
        continuation_preamble = (
            "A continuation must keep parallelizing remaining ready work; being a continuation\n"
            "is not a reason to fall back to sequential execution."
        )
        assert continuation_preamble in default_section, (
            "continuation parallel framing missing from extracted section under default caps"
        )
        assert continuation_preamble in alternate_section, (
            "continuation parallel framing missing from extracted section under alternate caps"
        )

    # Both renders must contain the parallel-by-default anchors and must
    # NOT contain the removed fallback phrases. This is the negative
    # rendered fallback assertion that closes the include / render gap
    # the source-level grep cannot cover.
    for pin in _COORDINATOR_PINS_REQUIRED:
        assert pin in default_section, (
            f"extracted section missing anchor {pin!r} under default caps in {template_name!r}"
        )
        assert pin in alternate_section, (
            f"extracted section missing anchor {pin!r} under alternate caps in {template_name!r}"
        )
        assert pin in default_rendered, (
            f"full render missing anchor {pin!r} under default caps in {template_name!r}"
        )
        assert pin in alternate_rendered, (
            f"full render missing anchor {pin!r} under alternate caps in {template_name!r}"
        )
    for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
        assert forbidden not in default_section, (
            f"parallel section re-introduced {forbidden!r} under default caps in {template_name!r}"
        )
        assert forbidden not in alternate_section, (
            f"parallel section re-introduced {forbidden!r} under alternate caps in {template_name!r}"
        )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_coordinator_full_render_excludes_removed_fallback_phrases(
    tmp_path: Path, template_name: str
) -> None:
    """The full coordinator render must not contain any removed fallback.

    A direct, in-render negative guard: the same forbidden phrases the
    partial source must not contain must also not appear anywhere in a
    full coordinator render, under either capability input. Catches
    regressions where a future include or template framing reintroduces
    the bounded-sequential escape hatch outside the parallel partial.
    """
    for caps in (
        SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        _maximally_different_caps(),
    ):
        rendered = _render_raw_with_caps(
            template_name, tmp_path, session_caps=caps
        )
        for forbidden in _COORDINATOR_RENDERED_NEGATIVES:
            assert forbidden not in rendered, (
                f"full {template_name!r} render contains forbidden phrase "
                f"{forbidden!r} under caps {caps!r}"
            )


# Feedback-path mandatory wording (Unit 1 S-2/S-3). These literals are
# the role-aware, conditionally accurate contract for the
# ``ANALYSIS_FEEDBACK_STATUS == 'request_changes'`` branch. The
# "MUST follow that division" anchor is the MUST-style mandatory
# wording; the explicit-plan-reason anchor is the
# sequentially-only-if-coupled claim; the "identifies independent
# units" anchor is the conditional accuracy (no false "the analysis
# above splits" claim when the feedback does not mention units).
#
# sub-anchors so a future partial rewrite that splits the sentence
# into shorter paragraphs still satisfies the contract.
_FEEDBACK_PATH_FOLLOW_PLAN_SENTENCE = (
    "Developer follow-plan: when the analysis above or the authoritative plan "
    "identifies independent units, you MUST follow that division and dispatch "
    "every ready unit in parallel \u2014 never handle them sequentially in this "
    "session or hand them back one at a time as a separate `partial` result. "
    "Sequential execution requires an explicit plan reason (declared coupling or "
    "`Depends on:` chains)."
)
_FEEDBACK_PATH_MUST_ANCHOR = (
    "you MUST follow that division and dispatch every ready unit in parallel"
)
_FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR = (
    "Sequential execution requires an explicit plan reason (declared coupling or "
    "`Depends on:` chains)"
)
_FEEDBACK_PATH_FALSE_CLAIM = "the analysis above splits"
_FEEDBACK_PATH_OPTIONAL_MAY_SPLIT = "may split the remaining work"
_FEEDBACK_PATH_WHEN_IT_DOES = "When it does, dispatch each unit in parallel"
# The pre-fix S-1 rendering bug produced this nested backtick sequence
# in coordinator renders; the fix collapses it to a single well-formed
# `` `partial` `` code span.
_FEEDBACK_PATH_BROKEN_BACKTICK = "`partial``"
@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_feedback_path_mandatory_wording_in_coordinator_renders(
    tmp_path: Path, template_name: str
) -> None:
    """S-2/S-3 (Unit 1): non-worker renders with ``request_changes`` must
    surface the new mandatory follow-plan sentence.

    The previous wording was conditional on the analysis actually
    splitting the work and used "may split ... When it does" framing,
    which leaked uncertainty about the feedback. The Unit 1 rewrite
    uses a MUST-style mandatory sentence and pairs it with the
    explicit-plan-reason anchor; the negative anchors below also
    close the "the analysis above splits" false-claim gap, the
    optional-phrasing gap, and the broken-backtick rendering bug
    from S-1.
    """
    rendered = _render_with_feedback(
        template_name,
        tmp_path,
        analysis_feedback_status="request_changes",
        analysis_feedback_content=(
            "Add a unit-aware guard to the S-2 and S-3 follow-plan branch "
            "so workers do not receive the orchestration sentence."
        ),
    )

    assert _FEEDBACK_PATH_FOLLOW_PLAN_SENTENCE in rendered, (
        f"coordinator {template_name!r} missing the mandatory follow-plan "
        f"sentence under request_changes feedback"
    )
    assert _FEEDBACK_PATH_MUST_ANCHOR in rendered, (
        f"coordinator {template_name!r} missing the MUST follow-plan anchor"
    )
    assert _FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR in rendered, (
        f"coordinator {template_name!r} missing the explicit-plan-reason anchor"
    )
    # Conditional accuracy + optional-phrasing sweep:
    assert _FEEDBACK_PATH_FALSE_CLAIM not in rendered, (
        f"coordinator {template_name!r} still asserts an unconditional "
        f"'the analysis above splits' claim that is not true for arbitrary "
        f"request_changes feedback"
    )
    assert _FEEDBACK_PATH_OPTIONAL_MAY_SPLIT not in rendered, (
        f"coordinator {template_name!r} kept the optional 'may split' framing"
    )
    assert _FEEDBACK_PATH_WHEN_IT_DOES not in rendered, (
        f"coordinator {template_name!r} kept the optional 'When it does' framing"
    )
    # S-1 rendering bug: the broken nested backtick must be gone.
    assert _FEEDBACK_PATH_BROKEN_BACKTICK not in rendered, (
        f"coordinator {template_name!r} render still has the broken nested "
        f"backtick sequence from the S-1 fix"
    )


@pytest.mark.parametrize("template_name", _COORDINATOR_TEMPLATES)
def test_feedback_path_renders_accurately_when_feedback_omits_units(
    tmp_path: Path, template_name: str
) -> None:
    """S-3 (Unit 1) / PA-003: the follow-plan sentence is correct even
    when ``request_changes`` feedback does not mention independent units.

    ``request_changes`` does not guarantee the feedback lists units;
    a coordinator render must not assert a fact the feedback does not
    establish. The new sentence is conditional on identifying
    independent units and pairs MUST-style action with a follow-up
    plan-reason requirement, so it is correct regardless of whether
    the analysis splits the work. The negative guards below also
    re-confirm the optional-phrasing sweep under feedback-without-
    units.
    """
    rendered = _render_with_feedback(
        template_name,
        tmp_path,
        analysis_feedback_status="request_changes",
        analysis_feedback_content=(
            "Tighten the typed contract on the render helper. The current "
            "shape leaks Optional[Workspace] in two places; switch to a "
            "narrower protocol and add a regression test."
        ),
    )

    # The conditional anchor "or the authoritative plan" must be
    # present: the sentence is honest about the feedback not naming
    # units and falls back to the plan as the source of any unit
    # declaration.
    assert "or the authoritative plan" in rendered, (
        f"coordinator {template_name!r} dropped the conditional "
        f"'or the authoritative plan' accuracy anchor"
    )
    assert _FEEDBACK_PATH_MUST_ANCHOR in rendered
    assert _FEEDBACK_PATH_EXPLICIT_REASON_ANCHOR in rendered
    # No false "the analysis above splits" claim, no optional phrasing.
    assert _FEEDBACK_PATH_FALSE_CLAIM not in rendered
    assert _FEEDBACK_PATH_OPTIONAL_MAY_SPLIT not in rendered
    assert _FEEDBACK_PATH_WHEN_IT_DOES not in rendered
