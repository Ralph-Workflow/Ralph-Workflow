from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    SiteDiscovery = Callable[[Path], list[tuple[str, str]]]


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
    kind: str
    site: str
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.site}: {self.message}"


def _inventory_path(package_root: Path) -> Path:
    return package_root / "testing" / "workspace_resource_inventory.json"


@dataclass(frozen=True)
class _SymbolSpan:
    qualified_name: str
    start_line: int
    end_line: int


def _module_symbols(tree: ast.Module) -> list[_SymbolSpan]:
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
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                )
    return spans


def _symbol_exists(rel_path: str, symbol: str, package_root: Path) -> bool:
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
    if ":" not in site:
        return None
    rel_path, _, symbol = site.partition(":")
    if not rel_path or not symbol:
        return None
    return rel_path, symbol


def _validate_owner_entries(
    owners: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
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
                    message=("workspace_owners entry requires a non-empty 'responsibilities' list"),
                )
            )
        elif isinstance(responsibilities, list):
            invalid = [
                str(item) for item in responsibilities if str(item) not in _OWNER_RESPONSIBILITIES
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
    violations: list[WorkspaceResourceInventoryViolation] = []
    for field in required_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="missing_field",
                    site=site or f"<{section_label}>",
                    message=(f"{section_label} entry requires a non-empty string field {field!r}"),
                )
            )
    return violations


def _validate_watch_entries(
    watches: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
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
            _validate_required_string_fields(entry, _REQUIRED_WATCH_FIELDS, site, "watch_consumers")
        )
        if site and site in seen_sites:
            violations.append(
                WorkspaceResourceInventoryViolation(
                    kind="duplicate_site",
                    site=site,
                    message=("duplicate 'site' key within watch_consumers; merge the entries"),
                )
            )
        if site:
            seen_sites.add(site)
    return violations


def _validate_storage_entries(
    storage: list[object],
) -> list[WorkspaceResourceInventoryViolation]:
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
                    message=("duplicate 'site' key within storage_classes; merge the entries"),
                )
            )
        if site:
            seen_sites.add(site)
    return violations


def _validate_structural(
    inventory: dict[str, object],
) -> list[WorkspaceResourceInventoryViolation]:
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
                message=("inventory JSON root must be an object with the three top-level arrays"),
            )
        ]
    return parsed, []


def _no_discovered_sites(_package_root: Path) -> list[tuple[str, str]]:
    return []


def audit_workspace_resource_inventory(
    package_root: Path,
    *,
    inventory_path: Path | None = None,
    site_discovery: SiteDiscovery = _no_discovered_sites,
) -> list[WorkspaceResourceInventoryViolation]:
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

    for audit_name, site in site_discovery(package_root):
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
