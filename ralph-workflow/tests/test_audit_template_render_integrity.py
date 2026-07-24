"""Tests for ``ralph.testing.audit_template_render_integrity``.

The audit renders every packaged top-level prompt template through the
real rendering path (registry + partials + ``render_template``) across
the main toggle scenarios (LAST_RETRY_ERROR, ANALYSIS_FEEDBACK,
HAS_GIT_WRITE, HIDE_ARTIFACT_SUBMISSION_GUIDANCE) and enforces five
render-integrity checks: no unrendered Jinja markers, include
resolution, no duplicated headings, no duplicated >=120-char
paragraphs, and no blank-line/doubled-label defects.

``test_audit_main_returns_zero_on_clean_tree`` is the verify-gate
wiring: it runs inside ``make test`` (a ``make verify`` step), so any
template regression fails the default gate.
"""

from __future__ import annotations

import pytest

import ralph.testing.audit_template_render_integrity as audit_module
from ralph.testing.audit_template_render_integrity import (
    check_rendered_prompt,
    collect_violations,
    stubbed_embed_variables,
)
from ralph.testing.audit_template_render_integrity import main as audit_main

_CLEAN_PROMPT = (
    "# Title\n\n"
    "## Section A\n\n"
    "Some body text.\n\n"
    "```bash\n# a shell comment\n# a shell comment\n```\n\n"
    "## Section B\n\n"
    "LABEL:\ncontent\n"
)


def test_clean_prompt_produces_no_violations() -> None:
    assert check_rendered_prompt("example", _CLEAN_PROMPT) == []


def test_detects_unrendered_jinja_markers() -> None:
    descriptions = check_rendered_prompt("example", "Body with {{ LEFTOVER }} and {% if x %}.")
    assert any("'{{'" in d for d in descriptions)
    assert any("'{%'" in d for d in descriptions)


def test_detects_duplicated_heading_outside_code_fences() -> None:
    rendered = "## Steps\n\nbody\n\n## Steps\n"
    descriptions = check_rendered_prompt("example", rendered)
    assert any("duplicated heading" in d and "## Steps" in d for d in descriptions)


def test_ignores_repeated_hash_lines_inside_code_fences() -> None:
    rendered = "## Steps\n\n```\n## Steps\n## Steps\n```\n"
    assert check_rendered_prompt("example", rendered) == []


def test_detects_duplicated_long_paragraph() -> None:
    paragraph = ("restated guidance sentence " * 6).strip()
    assert len(paragraph) >= 120
    rendered = f"intro\n\n{paragraph}\n\nmiddle\n\n{paragraph}\n"
    descriptions = check_rendered_prompt("example", rendered)
    assert any("duplicated paragraph" in d for d in descriptions)


def test_short_repeated_fragments_are_not_flagged() -> None:
    rendered = "intro\n\nshort repeated line\n\nmiddle\n\nshort repeated line\n"
    assert check_rendered_prompt("example", rendered) == []


def test_detects_blank_line_runs_of_three_or_more() -> None:
    descriptions = check_rendered_prompt("example", "a\n\n\n\nb\n")
    assert any("consecutive blank lines" in d for d in descriptions)
    assert check_rendered_prompt("example", "a\n\n\nb\n") == []


def test_detects_doubled_label_line() -> None:
    descriptions = check_rendered_prompt("example", "ANALYSIS FEEDBACK:\nANALYSIS FEEDBACK:\nbody\n")
    assert any("doubled label line" in d and "ANALYSIS FEEDBACK:" in d for d in descriptions)


def test_worker_developer_base_prompt_is_stubbed() -> None:
    """The deliberate-duplication allowlist entry: worker_developer embeds the
    full base developer prompt via ``{{ base_prompt }}``, so the audit renders
    the wrapper with a stub to keep its checks meaningful. Pins the entry so
    it cannot be silently dropped (which would let the audit flag the
    deliberate embed) or silently widened."""
    stubs = stubbed_embed_variables()
    assert set(stubs) == {"worker_developer"}
    assert set(stubs["worker_developer"]) == {"base_prompt"}


def test_audit_module_exposes_main_entry_point() -> None:
    """Audit must be runnable as ``python -m ralph.testing.audit_template_render_integrity``."""
    assert hasattr(audit_module, "main")


def test_main_exit_codes_follow_collect_violations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` must exit 0 on a clean run and 1 with the violation list
    printed when violations exist (the contract ``make verify`` relies on)."""
    monkeypatch.setattr(audit_module, "collect_violations", lambda: [])
    assert audit_main([]) == 0
    monkeypatch.setattr(
        audit_module,
        "collect_violations",
        lambda: ["x.jinja [baseline]: duplicated heading (x2): '## Steps'"],
    )
    assert audit_main([]) == 1
    captured = capsys.readouterr()
    assert "duplicated heading" in captured.out
    assert "x.jinja" in captured.out


@pytest.mark.timeout_seconds(5)
def test_audit_clean_on_current_templates() -> None:
    """Verify-gate wiring: every packaged template x scenario must render
    cleanly. On failure, the full violation list is surfaced so the failing
    template and check are immediately visible in the test output."""
    violations = collect_violations()
    assert violations == [], "render-integrity violations:\n" + "\n".join(violations)
