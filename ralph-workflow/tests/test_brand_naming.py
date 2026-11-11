from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

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
    sphinx_dir = REPO_ROOT / "docs" / "sphinx"
    if not sphinx_dir.exists():
        return []
    return list(sphinx_dir.rglob("*.md")) + list(sphinx_dir.rglob("*.rst"))


def _jinja_templates() -> list[Path]:
    templates_dir = REPO_ROOT / "ralph" / "prompts" / "templates"
    if not templates_dir.exists():
        return []
    return list(templates_dir.rglob("*.jinja")) + list(templates_dir.rglob("*.j2"))


_ALLOWLIST: list[tuple[str, str]] = [
    ("Ralph loop", "historical concept reference: the Ralph loop"),
    ("Ralph-loop", "historical concept reference: the Ralph-loop (hyphenated)"),
    ("original Ralph", "historical lineage reference: original Ralph"),
    (
        "Ralph-managed",
        "parallelization rework: 'Ralph-managed' is the canonical product term "
        "for the dormant fan-out machinery",
    ),
    ("Ralph fan-out", "parallelization rework: 'Ralph fan-out' is the legacy dispatch mode name"),
    (
        "Ralph-orchestrated",
        "deprecated-worker rework: 'Ralph-orchestrated' is the canonical product term "
        "for the removed worker execution model (see Deprecated section in concepts.md)",
    ),
    (
        "Ralph should run",
        "parallelization rework: contraction of 'Ralph Workflow should run' in planning prompt",
    ),
    (
        "Ralph would have run",
        "parallelization rework: contraction of 'Ralph Workflow would have run' in planning prompt",
    ),
    ("|  _ \\", "ASCII art banner line"),
    ("| |_) /", "ASCII art banner line"),
    ("|  _ <", "ASCII art banner line"),
    ("|_| \\_\\", "ASCII art banner line"),
    ("Ralph-Workflow.git", "codeberg repository path"),
    ("Ralph-Workflow/ralph-workflow", "codeberg repository path"),
    ("https://codeberg.org/RalphWorkflow/Ralph-Workflow", "codeberg repository URL"),
    ("https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new", "codeberg issue tracker URL"),
    ("https://github.com/Ralph-Workflow/Ralph-Workflow", "github repository URL"),
    ("Ralph-Workflow/Ralph-Workflow", "github repository path"),
    ("gitdb.net/Ralph-Workflow/Ralph-Workflow", "gitdb mirror path"),
    ("or watch Ralph\nWorkflow", "rst line-wrapped Ralph Workflow"),
    ("Ralph-Workflow-Pro", "third-party product name"),
    ("the Ralph entry", "MCP server entry name (JSON key)"),
    ("Ralph entry plus", "MCP server entry name (JSON key)"),
]

_BARE_RALPH_RE = re.compile(r"\bRalph\b(?! Workflow)")


def _line_is_allowed(line: str) -> bool:
    return any(substring in line for substring, _ in _ALLOWLIST)


@pytest.mark.parametrize(
    "path",
    _AUDIT_FILES + _sphinx_docs() + _jinja_templates(),
    ids=lambda path: str(path.relative_to(REPO_ROOT)),
)
def test_no_bare_ralph_in_user_facing_files(path: Path) -> None:
    assert path.exists(), f"MISSING FILE: {path.relative_to(REPO_ROOT)}"

    violations = [
        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()!r}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _BARE_RALPH_RE.search(line) and not _line_is_allowed(line)
    ]

    assert not violations, (
        "The following lines contain bare 'Ralph' (should be 'Ralph Workflow'):\n"
        + "\n".join(violations)
    )
