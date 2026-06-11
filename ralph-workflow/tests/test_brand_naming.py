"""Regression test: bare 'Ralph' (project name) must always appear as 'Ralph Workflow'.

This test walks a set of user-facing source files and asserts that every
capital-R 'Ralph' occurrence is either:
  - followed by ' Workflow' (correct full project name), or
  - part of one of two explicitly allowed historical references:
    'original Ralph' or 'Ralph loop', or
  - on an explicitly allowlisted line (e.g. ASCII art, internal CLI command literal).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files to audit for bare 'Ralph' branding (static list).
# wt-007-consolidate-display deleted ralph/banner.py; the banner text now
# lives in ralph/display/parallel_display.py, which is included separately
# in the audit list below.
_AUDIT_FILES: list[Path] = [
    REPO_ROOT / "ralph" / "policy" / "defaults" / "agents.toml",
    REPO_ROOT / "ralph" / "policy" / "defaults" / "artifacts.toml",
    REPO_ROOT / "ralph" / "policy" / "defaults" / "mcp.toml",
    REPO_ROOT / "ralph" / "policy" / "defaults" / "pipeline.toml",
    REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml",
    REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml",
    REPO_ROOT / "ralph" / "install.py",
    REPO_ROOT / "ralph" / "display" / "parallel_display.py",
    REPO_ROOT / "ralph" / "cli" / "commands" / "init.py",
    REPO_ROOT / "ralph" / "cli" / "commands" / "diagnose.py",
    REPO_ROOT / "ralph" / "cli" / "commands" / "run.py",
    REPO_ROOT / "ralph" / "cli" / "commands" / "cleanup.py",
    REPO_ROOT / "ralph" / "cli" / "main.py",
    REPO_ROOT / "ralph" / "config" / "welcome.py",
    REPO_ROOT / "ralph" / "policy" / "validation" / "__init__.py",
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
]


def _sphinx_docs() -> list[Path]:
    """Return all Sphinx documentation files (*.md and *.rst)."""
    sphinx_dir = REPO_ROOT / "docs" / "sphinx"
    if not sphinx_dir.exists():
        return []
    return list(sphinx_dir.rglob("*.md")) + list(sphinx_dir.rglob("*.rst"))


def _jinja_templates() -> list[Path]:
    """Return all Jinja template files (*.jinja and *.j2)."""
    templates_dir = REPO_ROOT / "ralph" / "prompts" / "templates"
    if not templates_dir.exists():
        return []
    return list(templates_dir.rglob("*.jinja")) + list(templates_dir.rglob("*.j2"))


# Substrings that make a line acceptable even when it contains bare 'Ralph'.
# Each entry is a tuple of (substring, reason).
_ALLOWLIST: list[tuple[str, str]] = [
    # Historical concept references — allowed only when describing lineage,
    # not when using 'Ralph' as the current product name.
    ("Ralph loop", "historical concept reference: the Ralph loop"),
    ("Ralph-loop", "historical concept reference: the Ralph-loop (hyphenated)"),
    ("original Ralph", "historical lineage reference: original Ralph"),
    # ASCII art logo lines — the banner spells out 'Ralph' as visual art
    ("|  _ \\", "ASCII art banner line"),
    ("| |_) /", "ASCII art banner line"),
    ("|  _ <", "ASCII art banner line"),
    ("|_| \\_\\", "ASCII art banner line"),
    # WELCOME_MESSAGE constant — the string value itself is 'Welcome to Ralph Workflow'
    # but the constant name 'WELCOME_MESSAGE' appears on a line with 'Ralph Workflow' already
    # README codeberg repo path — Ralph-Workflow is the repo name, not a project name usage
    ("Ralph-Workflow.git", "codeberg repository path"),
    ("Ralph-Workflow/ralph-workflow", "codeberg repository path"),
    ("https://codeberg.org/RalphWorkflow/Ralph-Workflow", "codeberg repository URL"),
    ("https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new", "codeberg issue tracker URL"),
    # GitHub mirror repo path — Ralph-Workflow is the repo name, not a project name usage
    ("https://github.com/Ralph-Workflow/Ralph-Workflow", "github repository URL"),
    ("Ralph-Workflow/Ralph-Workflow", "github repository path"),
    # GitDB mirrors the GitHub repo path — Ralph-Workflow is the repo name, not a project name usage
    ("gitdb.net/Ralph-Workflow/Ralph-Workflow", "gitdb mirror path"),
    # Line-wrapped "Ralph\nWorkflow" in RST source (text wrapping, not a violation)
    ("or watch Ralph\nWorkflow", "rst line-wrapped Ralph Workflow"),
]

# Pattern: a capital-R 'Ralph' word that is NOT followed by ' Workflow'
_BARE_RALPH_RE = re.compile(r"\bRalph\b(?! Workflow)")


def _line_is_allowed(line: str) -> bool:
    """Return True if the line is explicitly allowed despite containing bare 'Ralph'."""
    return any(substring in line for substring, _ in _ALLOWLIST)


def test_no_bare_ralph_in_user_facing_files() -> None:
    """Bare 'Ralph' (project name) must appear as 'Ralph Workflow' in user-facing files."""
    violations: list[str] = []

    all_files = _AUDIT_FILES + _sphinx_docs() + _jinja_templates()

    for path in all_files:
        if not path.exists():
            violations.append(f"MISSING FILE: {path.relative_to(REPO_ROOT)}")
            continue

        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _BARE_RALPH_RE.search(line) and not _line_is_allowed(line):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{lineno}: {line.strip()!r}")

    assert not violations, (
        "The following lines contain bare 'Ralph' (should be 'Ralph Workflow'):\n"
        + "\n".join(violations)
    )
