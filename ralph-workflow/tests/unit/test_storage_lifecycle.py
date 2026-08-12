"""Behavioral tests for side-effect-free workspace storage lifecycle planning."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.workspace.storage_lifecycle import inventory_storage, plan_cleanup

# The AST writer-discovery walk parses three production trees on its first
# call; under a saturated shard that one-time cost can brush the default
# 1.0 s per-test ceiling, so the two tests that trigger the cold walk carry
# an explicit 10 s marker (the walk itself is ~0.5 s warm; the headroom is
# for shard load, not real work).
_AST_WALK_TIMEOUT = pytest.mark.timeout_seconds(10)


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
    assert entries["temporary_data"]["count"] == 1
    assert not (tmp_path / ".agent" / "diagnostics").exists()


def test_cleanup_plan_only_selects_recreatable_inactive_storage(tmp_path: Path) -> None:
    _write(tmp_path / ".agent" / "ralph-explore" / "index.db")
    _write(tmp_path / ".agent" / "tmp" / "old-run" / "scratch.txt")
    _write(tmp_path / ".agent" / "tmp" / "active-run" / "scratch.txt")

    candidates = plan_cleanup(inventory_storage(tmp_path), active_run_id="active-run")

    assert {(candidate["category"], candidate["path"].name) for candidate in candidates} == {
        ("workspace_intelligence", "ralph-explore"),
        ("temporary_data", "old-run"),
    }
    assert (tmp_path / ".agent" / "ralph-explore").exists()
    assert (tmp_path / ".agent" / "tmp" / "old-run").exists()


# ---------------------------------------------------------------------------
# W7: AST-derived accumulating-writer discovery
# ---------------------------------------------------------------------------

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = (
    _PACKAGE_ROOT / "ralph" / "workspace",
    _PACKAGE_ROOT / "ralph" / "mcp" / "artifacts",
    _PACKAGE_ROOT / "ralph" / "mcp" / "explore",
)

#: Single-line literals above this length are docstrings or prose, not paths.
_MAX_PATH_LITERAL_LENGTH = 120

#: String literals that name an accumulating path under the workspace.
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

#: Canonical accumulating path segments the inventory must account for; each
#: is matched against an inventory path's parts rather than a substring so a
#: prose mention cannot register.
_CANONICAL_SEGMENTS: tuple[str, ...] = (
    "receipts",
    "tmp",
    "logs",
    "history",
    "ralph-explore",
    "state.db",
)

#: Scratch-prefix patterns (glob forms and the ``completion_seen_<run>``
#: template) all live under ``.agent`` itself or its ``tmp`` child; the
#: inventory's owning root is the ``.agent`` segment.
_SCRATCH_PREFIXES: tuple[str, ...] = (
    "agent_retry_",
    "completion_seen_",
    "codex-home-",
    "ralph-mcp-session-",
)


def _inventory_covers(literal: str, inventory_paths: tuple[Path, ...]) -> bool:
    """True when one canonical accumulating segment of ``literal`` is inventoried."""
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


#: Process-local cache for the AST walk; the scanned tree is immutable
#: mid-suite and the per-test budget cannot afford a second walk.
#: bounded-accumulator-ok: populated once per process by the AST walk
_LITERAL_CACHE: list[tuple[str, str]] = []


def _iter_accumulating_literals() -> list[tuple[str, str]]:
    """Walk the scanned roots in-process and return (literal, relative source path).

    Never invokes a subprocess: ``docs/ralph-workflow-policy/testing-policy.md``
    prohibits real subprocesses in default tests, so this AST walk replaces
    the prior ``git grep`` discovery baseline. Results are cached per
    process: the scanned source tree does not change mid-suite, and the
    1.0 s per-test budget cannot afford two full AST walks.
    """
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
    """The scanned roots still name every canonical accumulating literal."""
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
    """W7: every discovered writer-path literal resolves under an inventory path."""
    inventory = inventory_storage(tmp_path)
    inventory_paths = tuple(
        path for entry in inventory for path in entry["paths"]
    )
    for literal, source in _iter_accumulating_literals():
        assert _inventory_covers(literal, inventory_paths), (
            f"accumulating literal {literal!r} from {source} is not covered by "
            "inventory_storage"
        )


def test_inventory_exposes_the_five_category_contract(tmp_path: Path) -> None:
    """Every category row carries the five-category contract fields."""
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
