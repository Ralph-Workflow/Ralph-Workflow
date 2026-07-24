"""Audit: every single-shot artifact template uses the shared submission macro.

A single-shot artifact template is one that submits exactly one
``artifact_type`` via ``ralph_submit_md_artifact`` (or its alias) in a
single round-trip. The planning multi-step flow (submit_plan_section +
finalize_plan) is explicitly excluded — its staging protocol lives
in planning.jinja.

The shared macro ``shared/_artifact_submission.jinja`` is the single
source of truth for:

- the canonical submit tool alias,
- the ``artifact_type`` value,
- the format index reference,
- the MCP argument shape (``content`` is a native JSON object/array or a
  JSON-serialized string),
- the ``.agent/tmp/<artifact_type>.json`` fallback.

Without this audit, the per-template prose can drift (and historically
has — the 3 inconsistent tool-name variables and the ad-hoc
``SUBMIT_ARTIFACT_BARE_HINT`` shortcut in planning.jinja are the
exemplar drift). The macro is the single source; the audit guarantees
no single-shot template reinvents its own copy.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("ralph/prompts/templates")

# Single-shot templates: submit exactly one artifact_type via ralph_submit_md_artifact.
# Planning multi-step templates (planning.jinja, planning_fallback.jinja,
# planning_edit.jinja, planning_edit_fallback.jinja) are excluded — they use
# the submit_plan_section / finalize_plan staging flow, which is a different
# protocol. planning_analysis.jinja is also a single-shot analysis template
# (it submits a planning_analysis_decision artifact), so it IS included.
# ``developer_iteration_fallback.jinja`` is the minimal-broken-prompt
# variant emitted when MCP wiring is unavailable; it deliberately omits
# the shared macro because its tests assert no ``content_path`` /
# ``development_result`` references — the agent is being told the
# canonical submit path is broken, so the contract is "use the legacy
# write_file fallback" (handled in :mod:`ralph.agents.developer`).
SINGLE_SHOT_TEMPLATES: tuple[str, ...] = (
    "commit_cleanup.jinja",
    "commit_message.jinja",
    "commit_simplified.jinja",
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "development_analysis.jinja",
    "planning_analysis.jinja",
    "review.jinja",
    "review_analysis.jinja",
    "worker_developer.jinja",
)


def _read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def test_shared_artifact_submission_macro_exists() -> None:
    macro = TEMPLATES_DIR / "shared" / "_artifact_submission.j2"
    assert macro.exists(), (
        f"Missing {macro}; the single-source-of-truth submission macro is "
        f"required to keep every per-template prose block in sync."
    )
    content = macro.read_text(encoding="utf-8")
    assert "render_artifact_submission" in content, (
        "shared/_artifact_submission.j2 must export render_artifact_submission macro"
    )
    # The macro must mention every key contract element so each render call
    # cannot silently drop one.
    for required_token in (
        "artifact_type",
        "submit_tool_reference",
        ".agent/artifact-formats",
        ".agent/tmp/",
        "content",
    ):
        assert required_token in content, (
            f"shared/_artifact_submission.j2 must reference {required_token!r}"
        )


def test_every_single_shot_template_includes_shared_macro() -> None:
    for template_name in SINGLE_SHOT_TEMPLATES:
        content = _read_template(template_name)
        # The template MUST import the shared macro. The import line
        # uses Jinja ``from 'shared/_artifact_submission.j2' import ...``
        # (the .j2 extension matches how the partial loader registers
        # files; see ralph.prompts.template_registry).
        assert "shared/_artifact_submission" in content, (
            f"{template_name} is a single-shot artifact template but does not "
            f"include the shared submission macro. Use "
            f"{{% from 'shared/_artifact_submission.j2' import render_artifact_submission %}} "
            f"and call it with (artifact_type, submit_tool_reference) so the "
            f"tool name, format index, and fallback stay in sync with every "
            f"other single-shot template."
        )


def test_no_planning_multistep_template_uses_shared_macro() -> None:
    """Planning multi-step (submit_plan_section / finalize_plan) MUST NOT use the macro.

    The macro is for single-shot artifacts. The planning multi-step flow
    stages sections incrementally and finalizes; its instructions live in
    planning.jinja and must not be replaced by the single-shot block.
    """
    for excluded_name in (
        "planning.jinja",
        "planning_fallback.jinja",
        "planning_edit.jinja",
        "planning_edit_fallback.jinja",
    ):
        content = _read_template(excluded_name)
        assert "shared/_artifact_submission" not in content, (
            f"{excluded_name} is a multi-step planning template; it must NOT "
            f"include the single-shot shared macro. It uses submit_plan_section "
            f"and finalize_plan — those instructions live in the planning.jinja "
            f"protocol, not in the shared macro."
        )


def test_single_shot_templates_do_not_have_contradicting_tool_names() -> None:
    """Each single-shot template names a single canonical submit tool alias.

    The shared macro emits ``{{ submit_tool_reference }}`` (which the
    template passes in). A template that hard-codes a DIFFERENT submit
    tool name (e.g. ``submit_artifact`` instead of
    ``SUBMIT_ARTIFACT_TOOL_REFERENCE``) contradicts the macro contract.
    """
    for template_name in SINGLE_SHOT_TEMPLATES:
        content = _read_template(template_name)
        # Hard-coded tool names that contradict the macro contract.
        forbidden_tokens = (
            "MCP submit_artifact tool",  # ad-hoc shorthand
            "`submit_artifact`",  # bare tool name without the canonical alias
        )
        for token in forbidden_tokens:
            assert token not in content, (
                f"{template_name} uses the ad-hoc phrase {token!r}; replace it "
                f"with the shared macro so the tool name, format reference, "
                f"and fallback stay in sync with every other single-shot "
                f"template."
            )


def test_shared_macro_renders_canonical_tool_name() -> None:
    """The shared macro must accept the submit tool reference and use it verbatim.

    This pins the contract that ``submit_tool_reference`` is the rendered
    tool alias the runtime exposes (e.g. ``ralph_submit_md_artifact``), not
    a free-form string the template can lie about.
    """
    macro = (TEMPLATES_DIR / "shared" / "_artifact_submission.j2").read_text(encoding="utf-8")
    # The macro must reference ``submit_tool_reference`` (the parameter name)
    # and render it inside a backtick block so the agent sees the alias.
    assert "{{ submit_tool_reference }}" in macro, (
        "shared macro must interpolate {{ submit_tool_reference }} verbatim"
    )
    # And it must reference the artifact_type so each call can be distinct.
    assert "{{ artifact_type }}" in macro
