"""Regression tests for the workspace resource inventory completeness audit."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.testing import audit_workspace_resource_inventory as audit
from ralph.testing.audit_workspace_resource_inventory import (
    WorkspaceResourceInventoryViolation,
)


def _no_op_discovery(_package_root: Path) -> list[tuple[str, str]]:
    """Return no constituent-audit sites so synthetic tests skip the real walk."""
    return []


#: Each canonical primitive module paired with a real top-level symbol name
#: so a synthetic fake package can satisfy the backward (stale) check.
_CANONICAL_FAKE_MODULES: dict[str, str] = {
    "mcp/artifacts/idempotent_write.py": "write_text_if_changed",
    "mcp/artifacts/file_backend.py": "FileBackend",
    "mcp/artifacts/_path_file_backend.py": "PathFileBackend",
    "prompts/template_registry.py": "TemplateRegistry",
    "prompts/master_prompt.py": "materialize_master_prompt",
    "executor/process.py": "run_process",
    "process/manager/__init__.py": "_default_sync_process_factory",
    "process/manager/_process_manager.py": "ProcessManager",
}


def _write_fake_package(tmp_path: Path) -> Path:
    """Create a ``ralph/`` fake package whose canonical primitives resolve.

    Each canonical primitive module is written with a top-level ``def`` or
    ``class`` matching :data:`_CANONICAL_FAKE_MODULES` so the backward
    (stale) check passes against synthetic inventories.
    """
    package_root = tmp_path / "ralph"
    for rel, symbol in _CANONICAL_FAKE_MODULES.items():
        module = package_root / rel
        module.parent.mkdir(parents=True, exist_ok=True)
        if symbol[0].isupper():
            module.write_text(f"class {symbol}:\n    pass\n", encoding="utf-8")
        else:
            module.write_text(f"def {symbol}():\n    pass\n", encoding="utf-8")
    return package_root


def _write_inventory(package_root: Path, inventory: dict[str, object]) -> Path:
    """Write an inventory JSON under the testing tree of ``package_root``."""
    inventory_path = package_root / "testing" / "workspace_resource_inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory_path


def _complete_synthetic_inventory() -> dict[str, object]:
    """Return a structurally valid inventory covering every canonical primitive."""
    owners = [
        {
            "site": f"{rel}:{symbol}",
            "responsibilities": ["read"],
            "summary": f"owner for {rel}",
        }
        for rel, symbol in _CANONICAL_FAKE_MODULES.items()
    ]
    return {
        "workspace_owners": owners,
        "watch_consumers": [],
        "storage_classes": [],
    }


def test_missing_package_root_fails_closed(tmp_path: Path) -> None:
    """A absent package root cannot make completeness silently pass."""
    violations = audit.audit_workspace_resource_inventory(tmp_path / "absent")

    assert len(violations) == 1
    assert violations[0].kind == "missing_package_root"


def test_missing_inventory_fails_closed(tmp_path: Path) -> None:
    """An absent inventory file is itself drift."""
    package_root = tmp_path / "ralph"
    package_root.mkdir()

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert len(violations) == 1
    assert violations[0].kind == "missing_inventory"


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    """Unparseable inventory source cannot bypass completeness."""
    package_root = tmp_path / "ralph"
    package_root.mkdir()
    inventory_path = package_root / "testing" / "workspace_resource_inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text("{not valid json", encoding="utf-8")

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert len(violations) == 1
    assert violations[0].kind == "invalid_json"


def test_non_object_root_fails_closed(tmp_path: Path) -> None:
    """A JSON root missing the top-level arrays is structural drift."""
    package_root = tmp_path / "ralph"
    package_root.mkdir()
    empty_inventory: dict[str, object] = {}
    _write_inventory(package_root, empty_inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert "missing_top_level_array" in {v.kind for v in violations}


def test_missing_required_field_fails_closed(tmp_path: Path) -> None:
    """A workspace_owners entry without responsibilities is incomplete."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners.append({"site": "feature/owner.py:real", "summary": "no responsibilities"})
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "missing_field" for v in violations)


def test_invalid_responsibility_enum_fails_closed(tmp_path: Path) -> None:
    """A responsibility outside the fixed activity enum is rejected."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            "site": "feature/owner.py:real",
            "responsibilities": ["not_a_real_activity"],
            "summary": "bad enum",
        }
    )
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "invalid_enum" for v in violations)


def test_invalid_storage_category_enum_fails_closed(tmp_path: Path) -> None:
    """A storage category outside the five product categories is rejected."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    inventory["storage_classes"] = [
        {
            "site": "feature/owner.py:real",
            "category": "secret_sixth_category",
            "growth_trigger": "g",
            "user_value": "v",
            "owner": "o",
            "retention_bound": "r",
            "cleanup_result": "c",
            "recreatability": "re",
            "recovery_impact": "ri",
        }
    ]
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "invalid_enum" for v in violations)


def test_duplicate_site_within_array_fails_closed(tmp_path: Path) -> None:
    """Two workspace_owners entries with the same site are ambiguous."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            "site": "mcp/artifacts/file_backend.py:FileBackend",
            "responsibilities": ["read"],
            "summary": "duplicate",
        }
    )
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "duplicate_site" for v in violations)


def test_missing_watch_field_fails_closed(tmp_path: Path) -> None:
    """A watch_consumers entry missing a required contract field is rejected."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    inventory["watch_consumers"] = [
        {
            "site": "feature/owner.py:real",
            "scope": "s",
            "purpose": "p",
            "start_owner": "so",
            "stop_owner": "xo",
            "sharing": "sh",
            "exclusions": "e",
            # capacity_failure_behavior omitted
        }
    ]
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(
        v.kind == "missing_field" and "capacity_failure_behavior" in v.message for v in violations
    )


def test_missing_canonical_owner_fails_closed(tmp_path: Path) -> None:
    """A canonical primitive module with no inventory entry is uncovered."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners[:] = [
        entry
        for entry in owners
        if entry["site"] != "mcp/artifacts/file_backend.py:FileBackend"
    ]
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(
        v.kind == "missing_canonical_owner" and "file_backend.py" in v.site for v in violations
    )


def test_stale_inventory_site_fails_closed(tmp_path: Path) -> None:
    """An inventory entry referencing a non-existent symbol is stale."""
    package_root = _write_fake_package(tmp_path)
    module = package_root / "feature" / "owner.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("def real():\n    pass\n", encoding="utf-8")
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            "site": "feature/owner.py:DoesNotExist",
            "responsibilities": ["read"],
            "summary": "stale symbol",
        }
    )
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "stale_inventory_site" for v in violations)


def test_invalid_site_key_fails_closed(tmp_path: Path) -> None:
    """A site key with an empty symbol side is malformed."""
    package_root = _write_fake_package(tmp_path)
    inventory = _complete_synthetic_inventory()
    owners = inventory["workspace_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            # Colon present but empty symbol side: passes the structural
            # colon-presence check but fails the backward _split_site check.
            "site": "feature/owner.py:",
            "responsibilities": ["read"],
            "summary": "bad key",
        }
    )
    _write_inventory(package_root, inventory)

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert any(v.kind == "invalid_site_key" for v in violations)


def test_complete_synthetic_inventory_is_clean(tmp_path: Path) -> None:
    """A complete inventory over a well-formed fake package passes cleanly."""
    package_root = _write_fake_package(tmp_path)
    _write_inventory(package_root, _complete_synthetic_inventory())

    violations = audit.audit_workspace_resource_inventory(package_root, site_discovery=_no_op_discovery)

    assert violations == [], "; ".join(str(v) for v in violations)


def test_violation_str_format_is_stable() -> None:
    """The diagnostic line format must stay stable for downstream parsers."""
    violation = WorkspaceResourceInventoryViolation(
        kind="missing_field", site="x.py:X", message="needs field"
    )
    assert str(violation) == "[missing_field] x.py:X: needs field"
