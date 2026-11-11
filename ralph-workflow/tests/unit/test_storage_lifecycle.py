from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ralph.workspace.agent_dir_retention import (
    DEFAULT_MAX_AGE_SECONDS,
    prune_lock_run_ids,
    register_active_run,
)
from ralph.workspace.storage_lifecycle import inventory_storage, plan_cleanup

_AST_WALK_TIMEOUT = pytest.mark.timeout_seconds(20)


def _write(path: Path, content: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_inventory_reports_all_storage_categories_without_creating_paths(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "app.py", "print('ok')")
    _write(tmp_path / ".agent" / "receipts" / "run-1" / "receipt.json")
    _write(tmp_path / ".agent" / "ralph-explore" / "index.db")
    _write(tmp_path / ".agent" / "logs" / "run.log")
    _write(tmp_path / ".agent" / "tmp" / "codex-home-1" / "config.toml")

    entries = {entry["category"]: entry for entry in inventory_storage(tmp_path)}

    assert set(entries) == {
        "project_content",
        "workflow_records",
        "workspace_intelligence",
        "operational_records",
        "temporary_data",
    }
    assert entries["project_content"]["bytes"] == len("print('ok')")
    assert entries["workflow_records"]["count"] == 1
    assert entries["workspace_intelligence"]["bytes"] == len("data")
    assert entries["operational_records"]["count"] == 1
    assert entries["operational_records"]["max_age_seconds"] == DEFAULT_MAX_AGE_SECONDS
    assert entries["operational_records"]["recreatable"] is True
    assert entries["temporary_data"]["count"] == 1
    assert not (tmp_path / ".agent" / "diagnostics").exists()


def test_cleanup_plan_only_selects_recreatable_inactive_storage(tmp_path: Path) -> None:
    _write(tmp_path / ".agent" / "ralph-explore" / "index.db")
    _write(tmp_path / ".agent" / "tmp" / "old-run" / "scratch.txt")
    _write(tmp_path / ".agent" / "tmp" / "active-run" / "scratch.txt")

    candidates = plan_cleanup(inventory_storage(tmp_path), keep_run_ids=frozenset({"active-run"}))

    assert {(candidate["category"], candidate["path"].name) for candidate in candidates} == {
        ("workspace_intelligence", "ralph-explore"),
        ("temporary_data", "old-run"),
    }
    assert (tmp_path / ".agent" / "ralph-explore").exists()
    assert (tmp_path / ".agent" / "tmp" / "old-run").exists()


def test_operational_records_become_cleanup_eligible_after_age_bound(tmp_path: Path) -> None:
    aged_log = tmp_path / ".agent" / "logs" / "stale-run.log"
    _write(aged_log)
    current = time.time()
    aged_mtime = current - (DEFAULT_MAX_AGE_SECONDS + 3600)
    os.utime(aged_log, (aged_mtime, aged_mtime))

    candidates = plan_cleanup(
        inventory_storage(tmp_path),
        keep_run_ids=frozenset(),
        now=lambda: current,
    )

    candidate_paths = {candidate["path"] for candidate in candidates}
    assert aged_log in candidate_paths


def test_operational_records_keep_concurrent_active_run(tmp_path: Path) -> None:
    register_active_run(tmp_path, "run-current")
    active_runs_path = tmp_path / ".agent" / "active_runs.json"
    _write(active_runs_path, json.dumps({"run_ids": ["run-current", "run-other"]}))

    aged_log = tmp_path / ".agent" / "logs" / "run-other.log"
    _write(aged_log)
    current = time.time()
    aged_mtime = current - (DEFAULT_MAX_AGE_SECONDS + 3600)
    os.utime(aged_log, (aged_mtime, aged_mtime))

    keep_run_ids = prune_lock_run_ids(tmp_path)
    candidates = plan_cleanup(
        inventory_storage(tmp_path),
        keep_run_ids=keep_run_ids,
        now=lambda: current,
    )

    candidate_paths = {candidate["path"] for candidate in candidates}
    assert aged_log not in candidate_paths


def test_operational_records_keep_current_run(tmp_path: Path) -> None:
    fresh_log = tmp_path / ".agent" / "logs" / "current-run.log"
    _write(fresh_log)

    candidates = plan_cleanup(
        inventory_storage(tmp_path),
        keep_run_ids=frozenset({"current-run"}),
        now=time.time,
    )

    candidate_paths = {candidate["path"] for candidate in candidates}
    assert fresh_log not in candidate_paths


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = (
    _PACKAGE_ROOT / "ralph" / "workspace",
    _PACKAGE_ROOT / "ralph" / "mcp" / "artifacts",
    _PACKAGE_ROOT / "ralph" / "mcp" / "explore",
)

_MAX_PATH_LITERAL_LENGTH = 120

_ACCUMULATING_MARKERS: frozenset[str] = frozenset(
    {
        ".agent",
        "receipts",
        "completion_seen_",
        "agent_retry_",
        "codex-home-",
        "ralph-mcp-session-",
        "state.db",
        "ralph-explore",
        "tmp",
        "logs",
        "history",
        "cache",
    }
)

_CANONICAL_SEGMENTS: tuple[str, ...] = (
    "receipts",
    "tmp",
    "logs",
    "history",
    "ralph-explore",
    "state.db",
)

_SCRATCH_PREFIXES: tuple[str, ...] = (
    "agent_retry_",
    "completion_seen_",
    "codex-home-",
    "ralph-mcp-session-",
)


def _inventory_covers(literal: str, inventory_paths: tuple[Path, ...]) -> bool:
    for segment in _CANONICAL_SEGMENTS:
        if segment not in literal:
            continue
        for path in inventory_paths:
            if segment in path.parts:
                return True
    if (
        ".agent" in literal
        or "cache" in literal
        or any(prefix in literal for prefix in _SCRATCH_PREFIXES)
    ):
        return any(".agent" in path.parts for path in inventory_paths)
    return False


_LITERAL_CACHE: list[tuple[str, str]] = []


def _iter_accumulating_literals() -> list[tuple[str, str]]:
    if _LITERAL_CACHE:
        return list(_LITERAL_CACHE)
    import ast

    discovered: list[tuple[str, str]] = []
    for root in _SCAN_ROOTS:
        for source in sorted(root.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            relative = source.relative_to(_PACKAGE_ROOT).as_posix()
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                value = node.value
                if len(value) > _MAX_PATH_LITERAL_LENGTH or "\n" in value:
                    continue
                if any(marker in value for marker in _ACCUMULATING_MARKERS):
                    discovered.append((value, relative))
    _LITERAL_CACHE.extend(discovered)
    return discovered


@_AST_WALK_TIMEOUT
def test_ast_discovery_finds_the_canonical_writer_literals() -> None:
    literals = {value for value, _source in _iter_accumulating_literals()}
    canonical = (
        "receipts",
        "completion_seen_",
        "agent_retry_",
        "codex-home-",
        "ralph-mcp-session-",
        "state.db",
        "ralph-explore",
        "tmp",
        "logs",
        "history",
    )
    for marker in canonical:
        assert any(marker in value for value in literals), (
            f"canonical accumulating literal {marker!r} missing from the scanned roots"
        )


@_AST_WALK_TIMEOUT
def test_every_accumulating_literal_maps_to_an_inventory_entry(tmp_path: Path) -> None:
    inventory = inventory_storage(tmp_path)
    inventory_paths = tuple(path for entry in inventory for path in entry["paths"])
    for literal, source in _iter_accumulating_literals():
        assert _inventory_covers(literal, inventory_paths), (
            f"accumulating literal {literal!r} from {source} is not covered by inventory_storage"
        )


def test_inventory_exposes_the_five_category_contract(tmp_path: Path) -> None:
    inventory = inventory_storage(tmp_path)
    assert {entry["category"] for entry in inventory} == {
        "project_content",
        "workflow_records",
        "workspace_intelligence",
        "operational_records",
        "temporary_data",
    }
    for entry in inventory:
        for field in (
            "growth_trigger",
            "retention_basis",
            "eligibility_reason",
            "recreatable",
            "recovery_impact",
            "active_owner",
            "paths",
        ):
            assert field in entry, f"{entry['category']} missing field {field}"
