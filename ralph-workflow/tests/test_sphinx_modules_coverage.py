"""Test that every public ralph subpackage is covered in the Sphinx autodoc tree."""

from __future__ import annotations

from pathlib import Path

# Subpackages intentionally excluded from autodoc coverage.
# testing: internal test helpers, not part of the public API.
_EXCLUDED: frozenset[str] = frozenset({"testing"})

_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"


def _public_top_level_subpackages() -> list[str]:
    """Walk ralph/ and return top-level subpackage names (dirs with __init__.py)."""
    results: list[str] = []
    for entry in sorted(_RALPH_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue
        if entry.name in _EXCLUDED:
            continue
        if (entry / "__init__.py").exists():
            results.append(entry.name)
    return results


def test_all_public_subpackages_covered_in_modules_rst() -> None:
    """Every public top-level subpackage must have an automodule:: entry in modules.rst."""
    modules_text = _MODULES_RST.read_text(encoding="utf-8")
    subpackages = _public_top_level_subpackages()

    missing: list[str] = []
    for name in subpackages:
        automodule_target = f"automodule:: ralph.{name}"
        if automodule_target not in modules_text:
            missing.append(name)

    assert not missing, (
        "The following public subpackages are missing from docs/sphinx/modules.rst:\n"
        + "\n".join(f"  ralph.{name}" for name in missing)
        + "\n\nAdd an '.. automodule:: ralph.<name>' entry to modules.rst "
        "and update _EXCLUDED in this test if the subpackage is intentionally private."
    )
