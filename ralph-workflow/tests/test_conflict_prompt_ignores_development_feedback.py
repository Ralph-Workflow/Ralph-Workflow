from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt

if TYPE_CHECKING:
    from pathlib import Path

_GATE_FEEDBACK = (
    "Publication gate `make verify` failed (exit 2) on tree abc123; the run's commits "
    "already landed. Fix the failures below so the gate passes."
)


def _render(root: Path) -> str:
    prompt_path = render_conflict_prompt(
        root=root,
        target="main",
        conflicted_paths=("src/alpha.py",),
        round_index=1,
        round_cap=3,
        surviving_marker_paths=(),
    )
    assert prompt_path is not None
    return prompt_path.read_text(encoding="utf-8")


def test_development_gate_feedback_is_not_shown_to_the_resolver(tmp_path: Path) -> None:
    development_hint = tmp_path / ".agent" / "tmp" / "last_retry_error_development.txt"
    development_hint.parent.mkdir(parents=True)
    development_hint.write_text(_GATE_FEEDBACK, encoding="utf-8")

    rendered = _render(tmp_path)

    assert "Publication gate" not in rendered
    assert development_hint.read_text(encoding="utf-8") == _GATE_FEEDBACK
