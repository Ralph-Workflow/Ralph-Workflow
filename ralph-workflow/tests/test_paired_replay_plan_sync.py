from __future__ import annotations

import pytest

from tests.doc_roots import REPOSITORY_ROOT

_CANONICAL_PLAN_RELATIVE_PATH = "ralph-workflow/docs/agents/policy-v5-paired-replay-plan.md"
_MIRROR_PLAN_RELATIVE_PATH = "ralph-workflow/docs/plans/policy-v5-paired-replay-plan.md"

_PLAN_RELATIVE_PATHS = (
    _CANONICAL_PLAN_RELATIVE_PATH,
    _MIRROR_PLAN_RELATIVE_PATH,
)

_FORBIDDEN_NARRATION = (
    "v5 history entry",
    "the previous draft's",
)

_CITED_POLICY_SCHEMA_SOURCE = "ralph-workflow/ralph/project_policy/markers.py"
_CITED_POLICY_SCHEMA_SYMBOLS = (
    "SCHEMA_VERSION",
    "VERSION_SPECS",
)


def _read_bytes(relative_path: str) -> bytes:
    path = REPOSITORY_ROOT / relative_path
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_bytes()


def test_plan_copies_are_byte_identical() -> None:
    canonical = _read_bytes(_CANONICAL_PLAN_RELATIVE_PATH)
    mirror = _read_bytes(_MIRROR_PLAN_RELATIVE_PATH)
    assert canonical == mirror


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_copies_carry_no_replaced_draft_narration(relative_path: str) -> None:
    text = _read_bytes(relative_path).decode("utf-8")
    present = [phrase for phrase in _FORBIDDEN_NARRATION if phrase in text]
    assert not present, f"{relative_path} still carries narration text: {present}"


def test_cited_policy_schema_source_defines_the_cited_symbols() -> None:
    source = _read_bytes(_CITED_POLICY_SCHEMA_SOURCE).decode("utf-8")
    missing = [name for name in _CITED_POLICY_SCHEMA_SYMBOLS if name not in source]
    assert not missing, f"{_CITED_POLICY_SCHEMA_SOURCE} no longer defines: {missing}"
