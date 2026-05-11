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
from functools import cache, lru_cache
from pathlib import Path

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_BANNER_FILE = Path(__file__).parent.parent.parent / "ralph" / "banner.py"

_CONSOLE_ALLOWED = {"theme.py"}
_THEME_ALLOWED = {"theme.py"}
_ENV_ALLOWED = {"context.py", "content_condenser.py"}


@cache
def _code_only_lines(path: Path) -> frozenset[int]:
    """Return the set of 1-based line numbers that contain only code tokens.

    Lines that are exclusively comment or string tokens are excluded so that
    docstrings and comments do not trigger false positives.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    # Start with all non-empty line numbers.
    code_lines: set[int] = {i + 1 for i, ln in enumerate(lines) if ln.strip()}
    row_has_non_comment_or_string_token: dict[int, bool] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    except tokenize.TokenError:
        return frozenset(code_lines)

    ignored_types = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    string_or_comment_types = {tokenize.STRING, tokenize.COMMENT}

    for tok_type, _, (start_row, _), (end_row, _), _ in tokens:
        if tok_type in ignored_types:
            continue
        has_code = tok_type not in string_or_comment_types
        for row in range(start_row, end_row + 1):
            row_has_non_comment_or_string_token[row] = (
                row_has_non_comment_or_string_token.get(row, False) or has_code
            )

    non_code_lines = {
        row for row, has_code in row_has_non_comment_or_string_token.items() if not has_code
    }
    return frozenset(code_lines - non_code_lines)


@cache
def _scan_lines(path: Path) -> tuple[str, ...]:
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
    return tuple(result)


@lru_cache(maxsize=1)
def _all_display_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/display/ plus banner.py."""
    files = sorted(_DISPLAY_DIR.glob("*.py"))
    if _BANNER_FILE.exists():
        files.append(_BANNER_FILE)
    return tuple(files)


# Pre-populate caches at module import time so file I/O happens before
# the per-test SIGALRM window is set up.
for _f in _all_display_files():
    _scan_lines(_f)


def test_no_console_construction_outside_theme() -> None:
    """Console( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _CONSOLE_ALLOWED
        for line in _scan_lines(path)
        if "Console(" in line
    ]
    assert not violations, "Console( found outside theme.py (DI violation):\n" + "\n".join(
        violations
    )


def test_no_theme_construction_outside_theme() -> None:
    """Theme( must only appear in ralph/display/theme.py."""
    violations: list[str] = [
        f"{path.name}:{line.rstrip()}"
        for path in _all_display_files()
        if path.name not in _THEME_ALLOWED
        for line in _scan_lines(path)
        if "Theme(" in line
    ]
    assert not violations, "Theme( found outside theme.py (DI violation):\n" + "\n".join(violations)


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
