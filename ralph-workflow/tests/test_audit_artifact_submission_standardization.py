from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("ralph/prompts/templates")

SINGLE_SHOT_TEMPLATES: tuple[str, ...] = (
    "commit_review.jinja",
    "commit_message.jinja",
    "commit_simplified.jinja",
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "development_analysis.jinja",
    "planning_analysis.jinja",
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
        assert "shared/_artifact_submission" in content, (
            f"{template_name} is a single-shot artifact template but does not "
            f"include the shared submission macro. Use "
            f"{{% from 'shared/_artifact_submission.j2' import render_artifact_submission %}} "
            f"and call it with (artifact_type, submit_tool_reference) so the "
            f"tool name, format index, and fallback stay in sync with every "
            f"other single-shot template."
        )


def test_no_planning_multistep_template_uses_shared_macro() -> None:
    for excluded_name in (
        "planning.jinja",
        "planning_fallback.jinja",
        "planning_edit.jinja",
        "planning_edit_fallback.jinja",
    ):
        content = _read_template(excluded_name)
        assert "shared/_artifact_submission" not in content, (
            f"{excluded_name} is a multi-step planning template; it must NOT "
            f"include the single-shot shared macro. It uses "
            f"ralph_stage_md_artifact and ralph_finalize_md_artifact — those "
            f"instructions live in the planning protocol, not in the shared macro."
        )


def test_single_shot_templates_do_not_have_contradicting_tool_names() -> None:
    for template_name in SINGLE_SHOT_TEMPLATES:
        content = _read_template(template_name)
        forbidden_tokens = (
            "MCP submit_artifact tool",
            "`submit_artifact`",
        )
        for token in forbidden_tokens:
            assert token not in content, (
                f"{template_name} uses the ad-hoc phrase {token!r}; replace it "
                f"with the shared macro so the tool name, format reference, "
                f"and fallback stay in sync with every other single-shot "
                f"template."
            )


def test_shared_macro_renders_canonical_tool_name() -> None:
    macro = (TEMPLATES_DIR / "shared" / "_artifact_submission.j2").read_text(encoding="utf-8")
    assert "{{ submit_tool_reference }}" in macro, (
        "shared macro must interpolate {{ submit_tool_reference }} verbatim"
    )
    assert "{{ artifact_type }}" in macro
