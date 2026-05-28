#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
POSITIONING_PATH = ROOT / "agents/marketing/RALPH_WORKFLOW_POSITIONING.md"
CODEBERG_PRIMARY = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB_MIRROR = "https://github.com/Ralph-Workflow/Ralph-Workflow"
FIRST_TASK_GUIDE = "https://ralphworkflow.com/docs/first-task-guide"
START_HERE_GUIDE = "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/START_HERE.md"

FOUR_QUESTIONS: dict[str, str] = {
    "what_is_it": (
        "Ralph Workflow is the operating system for autonomous coding: "
        "a free and open-source composable loop framework and AI orchestrator."
    ),
    "who_is_it_for": (
        "Developers and technical teams doing ambitious software work that benefits "
        "from a structured workflow instead of a chat session."
    ),
    "why_different": (
        "It keeps a simple Ralph-loop core, then composes that core into planning, "
        "development, verification, and broader workflow loops with strong defaults."
    ),
    "why_now": (
        "You can use the default workflow as-is today, or build your own workflow on top "
        "without giving up control of your tools or process."
    ),
}

CORE_TRUTHS: tuple[str, ...] = (
    "the operating system for autonomous coding",
    "free and open-source",
    "composable loop framework",
    "ai orchestrator",
    "simple at the core",
    "powerful in composition",
    "strong default workflow for writing software",
    "use the default workflow as-is or build on top",
    "codeberg is primary",
)

# Phrases that were previously banned but are now replaced with better site language.
# Kept as reference for understanding what was retired.
_RETIRED_LEADS: tuple[str, ...] = (
    "reviewable result",      # replaced with "finish line" / "finished code" / "tested code"
    "reviewable output",      # replaced with specific output descriptions
    "reviewable work units",  # replaced with "finished code" / "bounded scope"
    "transcript archaeology", # replaced with "visible diff" / "what changed"
    "repo-native",           # replaced with "your own repo" / "Codeberg-primary"
    "would you merge it",    # replaced with "would you ship it" / "ready to review"
)

# The FORBIDDEN_LEADS list is now empty — phrasing rules have been moved
# into REDDIT_LEARNINGS.md which defines what IS allowed, not just what is banned.
# The validate_marketing_copy() function now relies on positive framing rules instead.
FORBIDDEN_LEADS: tuple[str, ...] = ()


def load_canonical_positioning() -> str:
    return POSITIONING_PATH.read_text(encoding="utf-8")


def repo_cta_footer() -> str:
    return (
        "\n\n---\n\n"
        "**Best evaluator path:** inspect [Codeberg]"
        f"({CODEBERG_PRIMARY}) first, use the [first-task guide]({FIRST_TASK_GUIDE}), "
        "run one bounded task tonight, and ask one question tomorrow morning: would you merge this? "
        f"If you want the repo walkthrough, start with [START_HERE]({START_HERE_GUIDE}). "
        f"GitHub mirror: [Ralph-Workflow]({GITHUB_MIRROR}). "
        "Ralph Workflow is free and open-source, with a strong default workflow you can use as-is or build on top."
    )


def directory_blurb() -> str:
    return (
        "Ralph Workflow is the operating system for autonomous coding: a free and open-source "
        "composable loop framework and AI orchestrator. It keeps a simple Ralph-loop core, ships "
        "with a strong default workflow for writing software, and lets teams use that default or "
        "build their own workflows on top."
    )


# Positive framing rules: what good marketing copy SHOULD contain.
# These replace the old forbidden-phrase approach with constructive guidance.
PREFERRED_PHRASES: tuple[str, ...] = (
    "finish",       # finish line, finished code, finished result
    "tested code",  # tested code, tests passed
    "strong default",  # strong default workflow
    "compose",      # composable, composition
    "your own repo",  # not "repo-native" — personal, owned
    "codeberg",     # Codeberg as primary
    "ship",         # ready to ship, what you can ship
    "overnight",    # unattended overnight runs
    "close the laptop",  # visceral unattended framing
)

REQUIRED_PHRASES_FOR_DEFAULT_WORKFLOW: tuple[str, ...] = (
    "strong default workflow",
    "use the default workflow as-is",
    "build on top",
    "extensible",
    "composable",
)


def validate_marketing_copy(text: str, *, require_default_workflow: bool = False) -> list[str]:
    lowered = text.lower()
    issues: list[str] = []

    # Must contain core product framing
    has_ralph_product = "ralph workflow" in lowered and (
        "orchestrat" in lowered or "free and open" in lowered or "free/open" in lowered
        or "runs the " in lowered or "agent cli" in lowered or "adds" in lowered
        or "enforces" in lowered or "spec-driven" in lowered or "checkpoint" in lowered
        or "handoff" in lowered or "loop" in lowered
    )
    has_official_framing = (
        "operating system for autonomous coding" in lowered
        or "composable loop framework" in lowered
    )
    if not has_ralph_product and not has_official_framing:
        issues.append("missing core product framing")

    # Check for preferred positive phrases (at least 2 should be present)
    preferred_count = sum(1 for p in PREFERRED_PHRASES if p in lowered)
    if preferred_count < 1:
        issues.append("missing positive framing — add finish/ship/test/default/composable language")

    # Default workflow check
    if require_default_workflow:
        has_default = any(p in lowered for p in REQUIRED_PHRASES_FOR_DEFAULT_WORKFLOW)
        if not has_default:
            issues.append("missing default-workflow / extensibility framing")

    return issues
