from __future__ import annotations

from pathlib import Path


def _starter_path(name: str) -> Path:
    candidates = [
        Path("ralph-workflow/ralph/project_policy/starters") / name,
        Path("ralph/project_policy/starters") / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


APPEARANCE_ASSERTION_PROHIBITION = (
    "An appearance assertion (CSS/class/style/DOM) is NOT evidence of design quality. "
    "Design proof requires captures graded visually via the criterion 8 verdict."
)

STARTER_NAMES = [
    "testing-policy.md",
    "design-system-policy.md",
    "ux-policy.md",
    "accessibility-policy.md",
]

VERDICT_AUTHORITY = (
    "Criterion 8 verdict authority: a capture-backed criterion 8 verdict is agent-produced "
    "evidence and does not close the design review lane; the named human review verdict "
    "remains required."
)


def test_all_four_starters_contain_prohibition_clause() -> None:
    for name in STARTER_NAMES:
        content = _starter_path(name).read_text(encoding="utf-8")
        assert APPEARANCE_ASSERTION_PROHIBITION in content, name


def test_all_four_starters_align_verdict_authority_statement() -> None:
    for name in STARTER_NAMES:
        content = _starter_path(name).read_text(encoding="utf-8")
        assert VERDICT_AUTHORITY in content, name
        assert "design_capture_command" in content, name
        assert "ralph://media/{artifact_id}" in content, name


def test_design_system_starter_has_one_review_authority_section() -> None:
    content = _starter_path("design-system-policy.md").read_text(encoding="utf-8")

    assert content.count("## Review authority") == 1


def test_adr_records_renderer_scope_prompt_scope_and_review_authority() -> None:
    candidates = [
        Path("ralph-workflow/docs/architecture/adr-0002-visual-design-verification.md"),
        Path("docs/architecture/adr-0002-visual-design-verification.md"),
    ]
    for candidate in candidates:
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            break
    else:
        raise AssertionError("ADR-0002 is missing")

    assert "web UI only" in content
    assert "bounded declared capture command" in content
    assert "no renderer or non-web UI" in content
    assert "developer prompt guidance only" in content.lower()
    assert "requires the named human review verdict" in content
