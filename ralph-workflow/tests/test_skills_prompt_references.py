"""Tests for canonical prompt skill references."""

from __future__ import annotations

from ralph.skills._content import BASELINE_SKILL_NAMES
from ralph.skills._prompt_skill_references import (
    development_skill_references_text,
    planning_skill_references_text,
    referenced_skill_names,
)


def test_referenced_skill_names_are_shipped_baseline_skills() -> None:
    assert set(referenced_skill_names()).issubset(set(BASELINE_SKILL_NAMES))


def test_planning_skill_references_text_mentions_expected_curated_skills() -> None:
    text = planning_skill_references_text()

    assert "`using-superpowers`" in text
    assert "`writing-plans`" in text
    assert "`brainstorming`" in text
    assert "`dispatching-parallel-agents`" in text
    assert "`subagent-driven-development`" in text
    assert "`coding-standards`" in text


def test_development_skill_references_text_mentions_expected_curated_skills() -> None:
    text = development_skill_references_text()

    assert "`using-superpowers`" in text
    assert "`test-driven-development`" in text
    assert "`systematic-debugging`" in text
    assert "`verification-before-completion`" in text
    assert "`requesting-code-review`" in text
    assert "`security-review`" in text
