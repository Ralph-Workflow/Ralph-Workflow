"""Behavioral tests for side-effect-free workspace storage lifecycle planning."""

from __future__ import annotations

from pathlib import Path

from ralph.workspace.storage_lifecycle import inventory_storage, plan_cleanup


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
