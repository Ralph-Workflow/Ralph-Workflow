"""Test that every public ralph module and package is covered in Sphinx autodoc."""

from __future__ import annotations

import re
from pathlib import Path

_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"

# Modules and packages intentionally excluded from autodoc coverage, with reasons.
# These are internal/helper modules that should not appear in the public API reference.
_EXCLUDED: dict[str, str] = {}

_TOP_LEVEL_SECTION_HEADERS = frozenset({
    "Top-Level",
    "CLI",
    "Config",
    "Policy",
    "Pipeline",
    "Phases",
    "Agents",
    "MCP",
    "Git",
    "Workspace",
    "Recovery",
    "Runtime",
    "Process",
    "API",
    "Utilities",
    "Testing",
})


def _walk_public_modules_and_packages(root: Path, prefix: str = "") -> list[str]:
    """Walk root and return all public module/package names.

    A public name is a directory with __init__.py or a .py file (not starting
    with _).  Names are returned as dot-separated qualified paths relative to
    the root (e.g. "mcp.server", "mcp.server.factory").
    """
    results: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("_"):
            continue

        if entry.suffix == ".py":
            # Leaf module (but not __main__.py which is entry-point only)
            if entry.name == "__main__.py":
                continue
            name = prefix + entry.stem if prefix else entry.stem
            results.append(name)
        elif entry.is_dir():
            if (entry / "__init__.py").exists():
                # Package: recurse into it
                child_prefix = prefix + entry.name + "." if prefix else entry.name + "."
                results.append(prefix + entry.name if prefix else entry.name)
                results.extend(_walk_public_modules_and_packages(entry, child_prefix))
    return results


def _extract_documented_modules(modules_rst_text: str) -> set[str]:
    """Extract module names from modules.rst RST source.

    Handles two forms:
      - ``.. automodule:: ralph.foo.bar``  (explicit automodule directive)
      - RST section-title lines like "ralph.mcp.server" or "ralph/mcp/server"
        that represent documented module headings.
    """
    documented: set[str] = set()

    # 1. Explicit automodule directives
    for directive in re.findall(
        r"^\.\. automodule:: (.+)$", modules_rst_text, re.MULTILINE
    ):
        module_name = directive.strip()
        # Normalise ralph.mcp -> mcp, ralph.mcp.server -> mcp.server
        if module_name.startswith("ralph."):
            documented.add(module_name[len("ralph.") :])
        else:
            documented.add(module_name)

    # 2. RST section-title lines that look like module paths.
    #    A title like "ralph.mcp.server" or "ralph/mcp/server" under the
    #    "API Reference" heading represents documentation for that module.
    #    We identify them by matching dotted or slash-separated Ralph paths.
    for rst_line in modules_rst_text.splitlines():
        line = rst_line.rstrip()
        # Skip directive lines
        if line.startswith(".. ") or line.startswith("   ") or line.startswith("\t"):
            continue
        # Skip RST field lists and comments
        if line.startswith(":") or line.startswith(".."):
            continue
        # A bare module-like title (letters, dots, slashes, underscores)
        if re.match(r"^[\w./]+$", line) and ("." in line or "/" in line):
            # Normalise slashes to dots and strip ralph. prefix
            normalised = line.replace("/", ".").strip(".")
            if normalised.startswith("ralph."):
                normalised = normalised[len("ralph.") :]
            if normalised and normalised not in _TOP_LEVEL_SECTION_HEADERS:
                documented.add(normalised)

    return documented


def test_all_public_modules_and_packages_covered_in_modules_rst() -> None:
    """Every public Python module/package under ralph/ must appear in modules.rst.

    This test inventories the complete public surface of the ralph package
    (all packages and leaf modules, excluding private/_-prefixed names and
    intentionally internal namespaces listed in _EXCLUDED) and verifies each
    has a corresponding entry in docs/sphinx/modules.rst.

    Entries in _EXCLUDED are checked for consistency: if modules.rst documents
    a name that is also in _EXCLUDED, the test fails to catch the policy
    disagreement.
    """
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    documented = _extract_documented_modules(modules_rst_text)

    # Build the full public surface
    all_names: list[str] = _walk_public_modules_and_packages(_RALPH_ROOT)

    # Filter out excluded namespaces
    def is_excluded(name: str) -> bool:
        return any(
            name == excluded or name.startswith(excluded + ".")
            for excluded in _EXCLUDED
        )

    public_names = [n for n in all_names if not is_excluded(n)]

    # Check for excluded modules that are still documented (policy disagreement)
    policy_disagreements: list[str] = []
    for excluded_name, reason in _EXCLUDED.items():
        # Check both the top-level package and any nested modules
        if excluded_name in documented:
            policy_disagreements.append(
                f"  ralph.{excluded_name} is documented in modules.rst "
                f"but _EXCLUDED says: {reason}"
            )
        # Also check nested modules of excluded packages
        policy_disagreements.extend(
            f"  ralph.{doc_name} is documented in modules.rst "
            f"but parent '{excluded_name}' is _EXCLUDED: {reason}"
            for doc_name in documented
            if doc_name.startswith(excluded_name + ".")
        )

    assert not policy_disagreements, (
        "Policy disagreement: modules.rst documents modules that are marked "
        "as intentionally undocumented in _EXCLUDED:\n"
        + "\n".join(policy_disagreements)
        + "\n\nEither remove these entries from modules.rst or remove them "
        "from _EXCLUDED."
    )

    # Find undocumented public modules
    missing = [
        name for name in sorted(public_names)
        if name not in documented
    ]

    assert not missing, (
        "The following public modules/packages are missing from "
        "docs/sphinx/modules.rst:\n"
        + "\n".join(f"  ralph.{name}" for name in missing)
        + "\n\nAdd corresponding entries to modules.rst and update _EXCLUDED "
        "in this test if the module is intentionally private."
    )
