from __future__ import annotations

from pathlib import Path

from ralph.project_policy import markers
from ralph.project_policy.starters import (
    STARTER_NAMES,
    __doc__,
    iter_starter_names,
    read_starter,
)

_STARTERS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "project_policy" / "starters"


def test_starter_count_matches_loader_truth() -> None:
    assert len(STARTER_NAMES) == 22
    assert len(STARTER_NAMES) == len(list(iter_starter_names()))
    assert len(markers.CORE_POLICY_FILES) == 12
    assert "execution-kernel.md" in markers.CORE_POLICY_FILES
    assert len(markers.CONDITIONAL_POLICY_FILES) == 10


def test_starter_docstring_states_v5_composition_without_recounting() -> None:
    assert "20 entries: 10 core + 10 conditional" not in __doc__
    assert "STARTER_NAMES" in __doc__
    assert "CORE_POLICY_FILES" in __doc__


def test_clean_code_starter_echoes_narrow_boundary_rule() -> None:
    content = read_starter("clean-code-policy.md")
    assert "narrowest useful observable boundary" in " ".join(content.split())
    assert "execution-kernel" in content
