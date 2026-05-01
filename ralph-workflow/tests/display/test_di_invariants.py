"""DI-invariant guard tests for ralph/display/.

Scans every Python file under ralph/display/ and ralph/banner.py to
assert that the single-source-of-truth contract is not violated:

- ``Console(`` must only appear in ``ralph/display/theme.py``.
- ``Theme(`` must only appear in ``ralph/display/theme.py``.
- ``os.environ`` and ``os.getenv`` must only appear in
  ``ralph/display/context.py`` and ``ralph/display/content_condenser.py``.

Lines that are part of comment or string tokens (including docstrings) are
excluded from the scan via ``tokenize``. Lines containing
``# noqa: di-allow`` are explicitly exempted.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_BANNER_FILE = Path(__file__).parent.parent.parent / "ralph" / "banner.py"

_CONSOLE_ALLOWED = {"theme.py"}
_THEME_ALLOWED = {"theme.py"}
_ENV_ALLOWED = {"context.py", "content_condenser.py"}


def _code_only_lines(path: Path) -> set[int]:
    """Return the set of 1-based line numbers that contain only code tokens.

    Lines that are exclusively comment or string tokens are excluded so that
    docstrings and comments do not trigger false positives.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    # Start with all non-empty line numbers
    code_lines: set[int] = {i + 1 for i, ln in enumerate(lines) if ln.strip()}
    string_or_comment_lines: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return code_lines
    for tok_type, _, (start_row, _), (end_row, _), _ in tokens:
        if tok_type in (tokenize.STRING, tokenize.COMMENT):
            for row in range(start_row, end_row + 1):
                string_or_comment_lines.add(row)
    # A line is "code only" if it has at least one non-string/comment token
    # We compute it as: lines that are NOT exclusively string/comment
    non_code_lines: set[int] = set()
    for row in string_or_comment_lines:
        # If every token on this row is string or comment, exclude it
        row_tokens = [
            t for t in tokens if t.start[0] <= row <= t.end[0]
            and t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                               tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER)
        ]
        if all(t.type in (tokenize.STRING, tokenize.COMMENT) for t in row_tokens):
            non_code_lines.add(row)
    return code_lines - non_code_lines


def _scan_lines(path: Path) -> list[str]:
    """Return code-only, non-exempted lines from path."""
    source_lines = path.read_text(encoding="utf-8").splitlines()
    code_line_nums = _code_only_lines(path)
    result: list[str] = []
    for lineno, line in enumerate(source_lines, start=1):
        if lineno not in code_line_nums:
            continue
        if "# noqa: di-allow" in line:
            continue
        result.append(line)
    return result


def _all_display_files() -> list[Path]:
    """Return all *.py files under ralph/display/ plus banner.py."""
    files = sorted(_DISPLAY_DIR.glob("*.py"))
    if _BANNER_FILE.exists():
        files.append(_BANNER_FILE)
    return files


@pytest.mark.timeout_seconds(5)
def test_no_console_construction_outside_theme() -> None:
    """Console( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _CONSOLE_ALLOWED
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, (
        "Console( found outside theme.py (DI violation):\n"
        + "\n".join(violations)
    )


@pytest.mark.timeout_seconds(5)
def test_no_theme_construction_outside_theme() -> None:
    """Theme( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _THEME_ALLOWED
        for line in _scan_lines(path)
        if "Theme(" in line
    ]
    assert not violations, (
        "Theme( found outside theme.py (DI violation):\n"
        + "\n".join(violations)
    )


@pytest.mark.timeout_seconds(5)
def test_no_env_reads_outside_allowed_modules() -> None:
    """os.environ and os.getenv must only appear in context.py and content_condenser.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _ENV_ALLOWED
        for line in _scan_lines(path)
        if "os.environ" in line or "os.getenv" in line
    ]
    assert not violations, (
        "os.environ/os.getenv found outside allowed modules (DI violation):\n"
        + "\n".join(violations)
    )
