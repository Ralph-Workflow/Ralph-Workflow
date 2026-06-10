"""Okabe-Ito discipline: no hardcoded hex color literals outside ralph/display/theme.py.

The Okabe-Ito palette is the single source of truth for Ralph's color
discipline. Any hex color literal (``#RRGGBB``) outside theme.py is a
drift hazard. This test is a black-box anti-drift guard that walks every
``.py`` file under ``ralph/display/`` (excluding ``theme.py``) and asserts
no line contains a hex color literal.

Allowed list: empty. The expected state is zero hits. If a legitimate
case arises in the future, the file:line pair must be added here with a
justifying comment.
"""

from __future__ import annotations

import re
from pathlib import Path

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_EXCLUDED_FILES: frozenset[str] = frozenset({"theme.py"})


def _walk_display_py_files() -> list[Path]:
    return sorted(p for p in _DISPLAY_DIR.rglob("*.py") if p.name not in _EXCLUDED_FILES)


def test_no_hex_colors_outside_theme() -> None:
    """Black-box anti-drift guard: zero hex color literals in ralph/display/ outside theme.py."""
    violations: list[str] = []
    for path in _walk_display_py_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _HEX_RE.search(line):
                violations.append(f"{path.relative_to(_DISPLAY_DIR)}:{lineno}:{line.rstrip()}")
    assert not violations, (
        "Hex color literal found outside ralph/display/theme.py "
        "(Okabe-Ito discipline violation):\n" + "\n".join(violations)
    )
