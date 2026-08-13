"""Workspace resource inventory completeness audit.

Ralph Workflow's workspace-awareness product (watches, change detection,
indexing, search, storage, retention, cleanup, recovery) is governed by five
existing fail-closed AST audits:

  * :func:`ralph.testing.audit_fsevents_watch_consolidation.audit_fsevents_watch_consolidation`
  * :func:`ralph.testing.audit_filesystem_polling_invocation.audit_filesystem_polling_invocation`
  * :func:`ralph.testing.audit_filesystem_read_consolidation.audit_filesystem_read_consolidation`
  * :func:`ralph.testing.audit_filesystem_write_consolidation.audit_filesystem_write_consolidation`
  * :func:`ralph.testing.audit_resource_lifecycle.audit_resource_lifecycle_directory`

Each of those audits flags a *raw* site that has neither a reasoned local
marker nor a canonical-primitive exemption. This audit layers a
product-level *inventory* over the same discovered surface: it requires
every discovered site (plus every canonical-primitive owner) to have a
structured entry in ``workspace_resource_inventory.json`` that names its
product role, storage category, watch contract, or cleanup outcome.

Completeness is mechanically decidable:

  * **Forward (uncovered)** -- every violation surfaced by the five
    constituent audits, resolved to its ``ralph-relative-path:qualified-symbol``
    site key, must have a matching inventory entry. The inventory therefore
    cannot silently drop a site that loses its marker.
  * **Canonical owners** -- every canonical-primitive production module must
    appear as an inventory ``site`` so the product map always explains where
    the shared filesystem boundaries live.
  * **Backward (stale)** -- every inventory ``site`` must resolve to a real
    module path whose source defines the named top-level symbol, so a
    refactor that deletes or renames an owner fails closed.
  * **Structural** -- top-level arrays, required per-entry fields, fixed
    responsibility / category enums, and duplicate ``site`` keys within one
    array are all checked.

The audit is AST + ``Path.read_text`` only (no subprocess, no ``time.sleep``,
no real filesystem mutation). It reuses the five constituent audits' public
functions rather than restating their AST rules, so the discovery surface
stays in lock-step with them.

Usage::

    python -m ralph.testing.audit_workspace_resource_inventory [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing import (
    audit_filesystem_polling_invocation as _polling,
)
from ralph.testing import (
    audit_filesystem_read_consolidation as _read,
)
from ralph.testing import (
    audit_filesystem_write_consolidation as _write,
)
from ralph.testing.audit_fsevents_watch_consolidation import (
    audit_fsevents_watch_consolidation as _fsevents,
)
from ralph.testing.audit_resource_lifecycle import (
    _default_roots,
)
from ralph.testing.audit_resource_lifecycle import (
    audit_resource_lifecycle_directory as _lifecycle,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    #: Injectable seam type alias for the constituent-audit discovery surface.
    #: Production uses :func:`_constituent_violation_sites`; tests inject a
    #: no-op or synthetic discovery so unit tests do not walk the real
    #: ~1000-file package (the resource-lifecycle audit's ``_default_roots()``
    #: is hardcoded to the real package and would exceed the per-test time
    #: budget). Mirrors the DI seams enforced by ``audit_di_seam.py``.
    SiteDiscovery = Callable[[Path], list[tuple[str, str]]]


#: Canonical production modules that the constituent audits exempt because
#: they ARE the shared filesystem / process / template boundaries. Each must
#: be documented as an inventory owner so the product map always names where
#: the consolidation primitives live. Testing-only audit modules are excluded
#: because this audit walks non-testing production modules only.
_CANONICAL_PRIMITIVE_MODULES: frozenset[str] = frozenset(
    {
        "mcp/artifacts/idempotent_write.py",
        "mcp/artifacts/file_backend.py",
        "mcp/artifacts/_path_file_backend.py",
        "prompts/template_registry.py",
        "prompts/master_prompt.py",
        "executor/process.py",
        "process/manager/__init__.py",
        "process/manager/_process_manager.py",
    }
)

#: Fixed responsibility enum for ``workspace_owners`` entries. Mirrors the
#: product-brief activity list (discovery, reading, writing, change awareness,
#: indexing, searching, retention, recovery, cleanup).
_OWNER_RESPONSIBILITIES: frozenset[str] = frozenset(
    {
        "discovery",
        "read",
        "write",
        "change",
        "index",
        "search",
        "retention",
        "recovery",
        "cleanup",
    }
)

#: Fixed storage-category enum for ``storage_classes`` entries. Mirrors the
#: five product storage categories exactly -- the audit rejects any other
#: value so the inventory cannot invent a sixth category.
_STORAGE_CATEGORIES: frozenset[str] = frozenset(
    {
        "project_content",
        "workflow_records",
        "workspace_intelligence",
        "operational_records",
        "temporary_data",
    }
)

_REQUIRED_WATCH_FIELDS: tuple[str, ...] = (
    "site",
    "scope",
    "purpose",
    "start_owner",
    "stop_owner",
    "sharing",
    "exclusions",
    "capacity_failure_behavior",
)

_REQUIRED_STORAGE_FIELDS: tuple[str, ...] = (
    "site",
    "category",
    "growth_trigger",
    "user_value",
    "owner",
    "retention_bound",
    "cleanup_result",
    "recreatability",
    "recovery_impact",
)

_TOP_LEVEL_ARRAYS: tuple[str, ...] = (
    "workspace_owners",
    "watch_consumers",
    "storage_classes",
)


@dataclass(frozen=True)
class WorkspaceResourceInventoryViolation:
    """A single workspace-resource-inventory audit violation."""

    kind: str
    site: str
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.site}: {self.message}"


def _inventory_path(package_root: Path) -> Path:
    """Return the canonical inventory JSON location under the testing tree."""
    return package_root / "testing" / "workspace_resource_inventory.json"


def _collect_python_files(root: Path) -> list[Path]:
    """Return every non-testing production ``*.py`` file under ``root``.

    Mirrors the constituent audits' walk: ``__pycache__`` is always skipped,
    and any ``testing`` directory relative to the package root is excluded so
    the audit covers production modules only.
    """
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if "__pycache__" in path.parts or "testing" in relative_parts:
            continue
        if not path.is_file():
            continue
        result.append(path)
    return result


@dataclass(frozen=True)
class _SymbolSpan:
    """A top-level or member symbol with its enclosing source line range."""

    qualified_name: str
    start_line: int
    end_line: int


def _module_symbols(tree: ast.Module) -> list[_SymbolSpan]:
    """Return top-level classes/functions and their methods/member functions.

    Each span records a ``qualified_name`` in ``TopLevel`` or
    ``TopLevel.member`` form and the inclusive ``[start_line, end_line]``
    range. ``<module>`` is synthesized by the caller for module-level sites.
    """
    spans: list[_SymbolSpan] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append(
                _SymbolSpan(
                    qualified_name=node.name,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
            if isinstance(node, ast.ClassDef):
                spans.extend(
                    _SymbolSpan(
                        qualified_name=f"{node.name}.{child.name}",
                        start_line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                    )
                    for child in node.body
                    if isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                )
    return spans


def _symbol_span_size(span: _SymbolSpan) -> int:
    """Return the inclusive line-count of a symbol span for innermost selection."""
    return span.end_line - span.start_line


def _resolve_site(rel_path: str, line: int, package_root: Path) -> str:
    """Return the ``rel_path:qualified-symbol`` site key for a violation line.

    Parses the module, finds the innermost top-level or member symbol whose
    line range covers ``line``, and returns ``rel_path:qualified_name``.
    Falls back to ``rel_path:<module>`` when the line is at module top level
    or the module cannot be resolved.
    """
    module_path = package_root / rel_path
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=rel_path)
    except (OSError, SyntaxError, ValueError):
        return f"{rel_path}:<module>"
    candidates = [
        span
        for span in _module_symbols(tree)
        if span.start_line <= line <= span.end_line
    ]
    if not candidates:
        return f"{rel_path}:<module>"
    # Innermost symbol = smallest enclosing range.
    innermost = min(candidates, key=_symbol_span_size)
    return f"{rel_path}:{innermost.qualified_name}"


def _symbol_exists(rel_path: str, symbol: str, package_root: Path) -> bool:
    """Return whether ``rel_path`` defines a top-level or member ``symbol``.

    Accepts ``TopLevel``, ``TopLevel.member``, or ``<module>``. Used by the
    backward (stale) check so a deleted/renamed owner fails closed.
    """
    if symbol == "<module>":
        return (package_root / rel_path).is_file()
    module_path = package_root / rel_path
    if not module_path.is_file():
        return False
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=rel_path)
    except (OSError, SyntaxError, ValueError):
        return False
    available = {span.qualified_name for span in _module_symbols(tree)}
    return symbol in available


def _split_site(site: str) -> tuple[str, str] | None:
    """Split a ``rel_path:symbol`` site key into ``(rel_path, symbol)``.

    Returns ``None`` when the key has no ``:`` separator or an empty side.
    """
    if ":" not in site:
        return None
    rel_path, _, symbol = site.partition(":")
    if not rel_path or not symbol:
        return None
    return rel_path, symbol


def _normalize_rel_path(file_path: str, package_root: Path) -> str:
    """Return a ``package_root``-relative posix path for a violation file_path.

    Constituent audits report paths inconsistently: fsevents returns paths
    relative to the ``ralph/`` package dir, polling/read/write return paths
    relative to the repo root (prefixed with ``ralph/``), and resource_lifecycle
    returns absolute paths. Normalize all three to a path relative to
    ``package_root`` so :func:`_resolve_site` can locate the module.
    """
    if file_path.startswith("/"):
        try:
            return Path(file_path).relative_to(package_root).as_posix()
        except ValueError:
            return Path(file_path).name
    return file_path.removeprefix("ralph/")


def _constituent_violation_sites(package_root: Path) -> list[tuple[str, str]]:
    """Return ``(audit_name, site_key)`` for every constituent-audit violation.

    Reuses the five public audit functions with the same roots that
    ``python -m ralph.testing.audit_*`` uses in ``make verify``: fsevents walks
    the ``ralph/`` package dir directly; polling/read/write resolve their
    ``ralph/`` default package root relative to the *repo* root
    (``package_root.parent``); and resource_lifecycle walks its private
    ``_default_roots()`` subdirectory list (which excludes ``testing/``).
    Keeping the invocation identical to ``make verify`` means the forward
    discovery surface stays in lock-step with the green gate.
    """
    repo_root = package_root.parent
    sites: list[tuple[str, str]] = []
    for audit_name, violations in (
        ("fsevents", _fsevents(package_root)),
        ("polling", _polling.audit_filesystem_polling_invocation(repo_root)),
        ("read", _read.audit_filesystem_read_consolidation(repo_root)),
        ("write", _write.audit_filesystem_write_consolidation(repo_root)),
    ):
        for violation in violations:
            rel = _normalize_rel_path(violation.file_path, package_root)
            sites.append((audit_name, _resolve_site(rel, violation.line, package_root)))
    for root in _default_roots():
        lifecycle_violations, _count = _lifecycle(root)
        for lc_violation in lifecycle_violations:
            rel = _normalize_rel_path(lc_violation.file_path, package_root)
            sites.append(("lifecycle", _resolve_site(rel, lc_violation.line, package_root)))
    return sites


# ---------------------------------------------------------------------------
# Structural validation helpers (one per inventory array)
# ---------------------------------------------------------------------------


def _validate_owner_entries(
    owners: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
    """Validate ``workspace_owners`` entries: fields, enum, duplicates."""
    violations: list[WorkspaceResourceInventoryViolation] = []
    seen_sites: set[str] = set()
    for entry in owners:
        if not isinstance(entry, dict):
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="invalid_entry",
                    site="<workspace_owners>",
                    message="each workspace_owners entry must be a JSON object",
                )
            )
            continue
        site = str(entry.get("site", ""))
        if not site or ":" not in site:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_site",
                    site=site or "<workspace_owners>",
                    message=(
                        "each workspace_owners entry needs a non-empty "
                        "'site' key of form 'ralph-relative-path:qualified-symbol'"
                    ),
                )
            )
        if site in seen_sites:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="duplicate_site",
                    site=site,
                    message=(
                        "duplicate 'site' key within workspace_owners; merge the "
                        "entries or split the responsibilities"
                    ),
                )
            )
        seen_sites.add(site)
        responsibilities = entry.get("responsibilities")
        if not isinstance(responsibilities, list) or not responsibilities:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_field",
                    site=site,
                    message=(
                        "workspace_owners entry requires a non-empty "
                        "'responsibilities' list"
                    ),
                )
            )
        elif isinstance(responsibilities, list):
            invalid = [
                str(item)
                for item in responsibilities
                if str(item) not in _OWNER_RESPONSIBILITIES
            ]
            if invalid:
                violations.append(
                    WorkspaceResourceInventoryViolation(
                        kind="invalid_enum",
                        site=site,
                        message=(
                            f"responsibilities {invalid!r} outside the fixed enum "
                            f"{sorted(_OWNER_RESPONSIBILITIES)}; use a valid activity"
                        ),
                    )
                )
    return violations


def _validate_required_string_fields(
    entry: dict[str, object],
    required_fields: tuple[str, ...],
    site: str,
    section_label: str,
) -> list[WorkspaceResourceInventoryViolation]:
    """Validate that every required field is a non-empty string."""
    violations: list[WorkspaceResourceInventoryViolation] = []
    for field in required_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_field",
                    site=site or f"<{section_label}>",
                    message=(
                        f"{section_label} entry requires a non-empty string "
                        f"field {field!r}"
                    ),
                )
            )
    return violations


def _validate_watch_entries(
    watches: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
    """Validate ``watch_consumers`` entries: fields, duplicates."""
    violations: list[WorkspaceResourceInventoryViolation] = []
    seen_sites: set[str] = set()
    for entry in watches:
        if not isinstance(entry, dict):
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="invalid_entry",
                    site="<watch_consumers>",
                    message="each watch_consumers entry must be a JSON object",
                )
            )
            continue
        site = str(entry.get("site", ""))
        violations.extend(
            _validate_required_string_fields(
                entry, _REQUIRED_WATCH_FIELDS, site, "watch_consumers"
            )
        )
        if site and site in seen_sites:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="duplicate_site",
                    site=site,
                    message=(
                        "duplicate 'site' key within watch_consumers; merge the entries"
                    ),
                )
            )
        if site:
            seen_sites.add(site)
    return violations


def _validate_storage_entries(
    storage: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
    """Validate ``storage_classes`` entries: fields, enum, duplicates."""
    violations: list[WorkspaceResourceInventoryViolation] = []
    seen_sites: set[str] = set()
    for entry in storage:
        if not isinstance(entry, dict):
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="invalid_entry",
                    site="<storage_classes>",
                    message="each storage_classes entry must be a JSON object",
                )
            )
            continue
        site = str(entry.get("site", ""))
        violations.extend(
            _validate_required_string_fields(
                entry, _REQUIRED_STORAGE_FIELDS, site, "storage_classes"
            )
        )
        category = entry.get("category")
        if isinstance(category, str) and category not in _STORAGE_CATEGORIES:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="invalid_enum",
                    site=site,
                    message=(
                        f"category {category!r} outside the fixed enum "
                        f"{sorted(_STORAGE_CATEGORIES)}; use one of the five "
                        "product storage categories"
                    ),
                )
            )
        if site and site in seen_sites:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="duplicate_site",
                    site=site,
                    message=(
                        "duplicate 'site' key within storage_classes; merge the entries"
                    ),
                )
            )
        if site:
            seen_sites.add(site)
    return violations


def _validate_structural(
    inventory: dict[str, object],
) -> list[WorkspaceResourceInventoryViolation]:
    """Validate top-level arrays, required fields, enums, and duplicate sites."""
    violations: list[WorkspaceResourceInventoryViolation] = []

    for array_name in _TOP_LEVEL_ARRAYS:
        section = inventory.get(array_name)
        if not isinstance(section, list):
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_top_level_array",
                    site=array_name,
                    message=(
                        f"inventory top-level key {array_name!r} must be a list of "
                        "entries; restore the array so the product map is complete"
                    ),
                )
            )
            return violations

    owners = inventory.get("workspace_owners", [])
    if isinstance(owners, list):
        violations.extend(_validate_owner_entries(owners))

    watches = inventory.get("watch_consumers", [])
    if isinstance(watches, list):
        violations.extend(_validate_watch_entries(watches))

    storage = inventory.get("storage_classes", [])
    if isinstance(storage, list):
        violations.extend(_validate_storage_entries(storage))

    return violations


def _all_inventory_sites(inventory: dict[str, object]) -> set[str]:
    """Return every ``site`` value across all three inventory arrays."""
    sites: set[str] = set()
    for array_name in _TOP_LEVEL_ARRAYS:
        section = inventory.get(array_name)
        if isinstance(section, list):
            for entry in section:
                if isinstance(entry, dict):
                    site = entry.get("site")
                    if isinstance(site, str) and site:
                        sites.add(site)
    return sites


def _inventory_site_paths(inventory_sites: set[str]) -> set[str]:
    """Return the ``rel_path`` component of every inventory site key.

    Used by the canonical-owner check so the inventory may document whichever
    symbol best represents the module (e.g. ``TemplateRegistry`` rather than
    the first private helper), while still proving the module is covered.
    """
    paths: set[str] = set()
    for site in inventory_sites:
        split = _split_site(site)
        if split is not None:
            paths.add(split[0])
    return paths


def _load_inventory(
    package_root: Path,
    json_path: Path,
) -> tuple[dict[str, object] | None, list[WorkspaceResourceInventoryViolation]]:
    """Read and parse the inventory JSON; return ``(inventory, violations)``.

    On any read/parse/type failure, returns ``(None, [violation])`` so the
    caller can short-circuit. On success returns ``(parsed_dict, [])``.
    """
    if not json_path.is_file():
        return None, [
            WorkspaceResourceInventoryViolation(
                kind="missing_inventory",
                site=str(json_path),
                message=(
                    "workspace_resource_inventory.json is absent; add it under "
                    "ralph/testing/ so every discovered site has a product entry"
                ),
            )
        ]

    try:
        raw = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [
            WorkspaceResourceInventoryViolation(
                kind="unreadable_inventory",
                site=str(json_path),
                message=f"inventory could not be read ({exc}); audit fails closed",
            )
        ]
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [
            WorkspaceResourceInventoryViolation(
                kind="invalid_json",
                site=str(json_path),
                message=f"inventory is not valid JSON ({exc}); audit fails closed",
            )
        ]
    if not isinstance(parsed, dict):
        return None, [
            WorkspaceResourceInventoryViolation(
                kind="invalid_inventory_root",
                site=str(json_path),
                message=(
                    "inventory JSON root must be an object with the three "
                    "top-level arrays"
                ),
            )
        ]
    return parsed, []


def audit_workspace_resource_inventory(
    package_root: Path,
    *,
    inventory_path: Path | None = None,
    site_discovery: SiteDiscovery | None = None,
) -> list[WorkspaceResourceInventoryViolation]:
    """Walk the inventory and constituent audits; return all violations.

    Args:
        package_root: The ``ralph/`` package root whose production modules and
            inventory are audited.
        inventory_path: Optional override for the inventory JSON location.
            Defaults to ``testing/workspace_resource_inventory.json`` under
            ``package_root``.
        site_discovery: Optional injectable seam returning
            ``(audit_name, site_key)`` pairs for the forward (uncovered) check.
            Defaults to :func:`_constituent_violation_sites` (the five
            constituent audits). Tests inject a no-op so they do not walk the
            real package.

    Returns:
        A list of :class:`WorkspaceResourceInventoryViolation` records. An
        empty list means the inventory is structurally valid, covers every
        constituent-audit discovery and canonical primitive owner, and has no
        stale entries.
    """
    if not package_root.is_dir():
        return [
            WorkspaceResourceInventoryViolation(
                kind="missing_package_root",
                site=str(package_root),
                message=(
                    "package root does not exist or is not a directory; the "
                    "inventory audit cannot prove completeness"
                ),
            )
        ]

    json_path = inventory_path if inventory_path is not None else _inventory_path(package_root)
    inventory, load_violations = _load_inventory(package_root, json_path)
    if inventory is None:
        return load_violations

    violations = _validate_structural(inventory)
    if violations:
        return violations

    inventory_sites = _all_inventory_sites(inventory)

    # Forward check: every constituent-audit violation must have an inventory
    # entry. When the constituent audits are clean (the normal green state),
    # this is vacuously satisfied; the moment a marker is removed, the
    # uncovered site must be documented here or re-marked.
    discover = site_discovery if site_discovery is not None else _constituent_violation_sites
    for audit_name, site in discover(package_root):
        if site not in inventory_sites:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="uncovered_discovered_site",
                    site=site,
                    message=(
                        f"site flagged by {audit_name} audit has no inventory entry; "
                        "add a workspace_owners/watch_consumers/storage_classes entry "
                        "or restore the reasoned local marker"
                    ),
                )
            )

    # Canonical owners: every canonical-primitive production module must
    # appear as an inventory site so the product map always names the shared
    # filesystem/process/template boundaries.
    inventory_paths = _inventory_site_paths(inventory_sites)
    for rel_path in sorted(_CANONICAL_PRIMITIVE_MODULES):
        if rel_path not in inventory_paths:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_canonical_owner",
                    site=rel_path,
                    message=(
                        f"canonical primitive module {rel_path!r} has no inventory "
                        "entry; document one of its owning symbols as a "
                        "workspace_owners/storage_classes entry"
                    ),
                )
            )

    # Backward check: every inventory site must resolve to a real module and
    # symbol so a refactor that deletes/renames an owner fails closed.
    for site in sorted(inventory_sites):
        split = _split_site(site)
        if split is None:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="invalid_site_key",
                    site=site,
                    message=(
                        "site key must be 'ralph-relative-path:qualified-symbol' with "
                        "non-empty sides"
                    ),
                )
            )
            continue
        rel_path, symbol = split
        if not _symbol_exists(rel_path, symbol, package_root):
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="stale_inventory_site",
                    site=site,
                    message=(
                        "inventory site no longer resolves to a defined module symbol; "
                        "update or remove the entry to match the current source"
                    ),
                )
            )

    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns 0 when clean, 1 on violations, 2 on bad root."""
    selected = sys.argv[1:] if argv is None else argv
    package_root = Path(selected[0]) if selected else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations = audit_workspace_resource_inventory(package_root)
    if violations:
        print(f"WORKSPACE RESOURCE INVENTORY VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            "Fix the inventory: ensure workspace_resource_inventory.json covers every "
            "discovered site and canonical primitive owner, has valid enum values, "
            "no duplicate site keys, and no stale entries."
        )
        return 1

    print("workspace resource inventory audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
