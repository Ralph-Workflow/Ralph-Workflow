"""Canonical prompt-facing references for shipped skills."""

from __future__ import annotations

from typing import NamedTuple

from ralph.skills._content import BASELINE_SKILL_NAMES


class PromptSkillReference(NamedTuple):
    skill_names: tuple[str, ...]
    guidance: str


_PLANNING_REFERENCES: tuple[PromptSkillReference, ...] = (
    PromptSkillReference(
        ("using-superpowers",),
        "read at the start of every planning task",
    ),
    PromptSkillReference(
        ("writing-plans",),
        "apply when creating multi-step implementation plans",
    ),
    PromptSkillReference(
        ("brainstorming",),
        "apply before open-ended or creative solution shaping",
    ),
    PromptSkillReference(
        ("executing-plans",),
        "apply when converting an approved written plan into execution guidance",
    ),
    PromptSkillReference(
        ("dispatching-parallel-agents", "subagent-driven-development"),
        "apply when work can be parallelized safely",
    ),
    PromptSkillReference(
        ("coding-standards",),
        "apply when the plan sets implementation quality bars",
    ),
    PromptSkillReference(
        ("verification-loop",),
        "apply when the plan defines verification requirements",
    ),
    PromptSkillReference(
        ("security-review",),
        "apply as a baseline quality control",
    ),
)

_DEVELOPMENT_REFERENCES: tuple[PromptSkillReference, ...] = (
    PromptSkillReference(
        ("using-superpowers",),
        "read at the start of every developer task",
    ),
    PromptSkillReference(
        ("test-driven-development",),
        "apply for feature work and bugfix work",
    ),
    PromptSkillReference(
        ("systematic-debugging",),
        "apply for errors, regressions, and failing verification",
    ),
    PromptSkillReference(
        ("verification-before-completion",),
        "apply before any completion or success claim",
    ),
    PromptSkillReference(
        ("requesting-code-review",),
        "apply before merge-ready or handoff-ready completion",
    ),
    PromptSkillReference(
        ("receiving-code-review",),
        "apply when acting on review findings",
    ),
    PromptSkillReference(
        ("security-review", "verification-loop", "coding-standards"),
        "apply as baseline quality controls",
    ),
    PromptSkillReference(
        ("using-git-worktrees",),
        "apply when isolated feature work or risky parallel work is required",
    ),
    PromptSkillReference(
        ("finishing-a-development-branch",),
        "apply when implementation is complete and integration choices must be made",
    ),
)


def _validate_reference_names(references: tuple[PromptSkillReference, ...]) -> None:
    baseline_names = set(BASELINE_SKILL_NAMES)
    for reference in references:
        for name in reference.skill_names:
            if name not in baseline_names:
                msg = f"Prompt skill reference names unknown shipped skill: {name}"
                raise ValueError(msg)


def _render_reference(reference: PromptSkillReference) -> str:
    rendered_names = " or ".join(f"**`{name}`**" for name in reference.skill_names)
    return f"- {rendered_names} — {reference.guidance}"


def _render_references(references: tuple[PromptSkillReference, ...]) -> str:
    _validate_reference_names(references)
    return "\n".join(_render_reference(reference) for reference in references)


def planning_skill_references_text() -> str:
    return _render_references(_PLANNING_REFERENCES)


def development_skill_references_text() -> str:
    return _render_references(_DEVELOPMENT_REFERENCES)


def plan_skill_references_text(skill_names: tuple[str, ...]) -> str:
    if not skill_names:
        return ""
    baseline_names = set(BASELINE_SKILL_NAMES)
    unknown = [name for name in skill_names if name not in baseline_names]
    if unknown:
        msg = f"Prompt skill reference names unknown shipped skill: {unknown[0]}"
        raise ValueError(msg)
    lines = [f"- **`{name}`** — recommended by the planner for this task" for name in skill_names]
    return "\n".join(lines)


def referenced_skill_names() -> tuple[str, ...]:
    seen: list[str] = []
    for reference in (*_PLANNING_REFERENCES, *_DEVELOPMENT_REFERENCES):
        for name in reference.skill_names:
            if name not in seen:
                seen.append(name)
    return tuple(seen)


__all__ = [
    "development_skill_references_text",
    "plan_skill_references_text",
    "planning_skill_references_text",
    "referenced_skill_names",
]
