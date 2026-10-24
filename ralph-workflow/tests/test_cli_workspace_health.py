"""Black-box tests for ``ralph workspace-health`` (AC-11).

Drives ``typer.testing.CliRunner.invoke(app, ["workspace-health",
"--workspace", str(tmp_path)])`` and asserts the rendered JSON carries
every AC-11 key with real values drawn from the controlled seams
(storage inventory, awareness snapshot, cleanup planner). The command
is read-only: it must not mutate the workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer.testing

from ralph.cli.main import app

_CATEGORY_FIXTURES: tuple[
    tuple[str, tuple[str, ...], str, str, str, str, bool, str, str, float | None],
    ...,
] = (
    (
        "project_content",
        (".",),
        "user source and project content",
        "user changes",
        "explicit user retention",
        "project content is never cleanup eligible",
        False,
        "loss is unrecoverable",
        "user",
        None,
    ),
    (
        "workflow_records",
        (
            ".agent/receipts",
            ".agent/state.db",
            ".agent/artifacts/history",
            ".agent/prompt_history",
            ".agent/raw",
        ),
        "workflow receipts and recoverable state",
        "workflow runs",
        "required recovery evidence",
        "required records are never cleanup eligible",
        False,
        "needed to recover workflows",
        "workflow",
        None,
    ),
    (
        "workspace_intelligence",
        (".agent/ralph-explore",),
        "derived workspace intelligence",
        "indexing",
        "rebuildable derived data",
        "derived intelligence can be rebuilt",
        True,
        "rebuilt on next index",
        "explore",
        None,
    ),
    (
        "operational_records",
        (".agent/logs",),
        "operational logs and diagnostics",
        "run diagnostics",
        "required operational evidence",
        "operational records older than the age bound are recreatable",
        True,
        "needed for diagnosis",
        "operations",
        1800.0,
    ),
    (
        "temporary_data",
        (".agent/tmp",),
        "interrupted-run temporary data",
        "run scratch creation",
        "inactive run ownership",
        "inactive temporary data can be discarded",
        True,
        "interrupted work is restarted",
        "run",
        None,
    ),
)


def _fake_inventory_storage(workspace_root: Path) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "category": category,
            "path": workspace_root / relatives[0],
            "paths": tuple(workspace_root / relative for relative in relatives),
            "bytes": 6 if category == "project_content" else 0,
            "count": 1 if category == "project_content" else 0,
            "purpose": purpose,
            "growth_trigger": trigger,
            "retention_basis": retention,
            "eligibility_reason": eligibility,
            "recreatable": recreatable,
            "recovery_impact": impact,
            "active_owner": owner,
            "max_age_seconds": max_age_seconds,
        }
        for (
            category,
            relatives,
            purpose,
            trigger,
            retention,
            eligibility,
            recreatable,
            impact,
            owner,
            max_age_seconds,
        ) in _CATEGORY_FIXTURES
    )


@pytest.fixture(autouse=True)
def _patch_inventory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.diagnostics import workspace_health as diag_module

    monkeypatch.setattr(diag_module, "inventory_storage", _fake_inventory_storage)


_runner = typer.testing.CliRunner()

_REQUIRED_TOP_LEVEL_KEYS = (
    "storage",
    "freshness",
    "readiness",
    "coverage_gaps",
    "active_observation",
    "active_refresh",
    "active_cleanup",
    "active_recovery",
    "watch_capacity",
    "cleanup_eligibility",
    "recreatability",
)

_FIVE_CATEGORIES = (
    "project_content",
    "workflow_records",
    "workspace_intelligence",
    "operational_records",
    "temporary_data",
)

_READINESS_TOKENS = frozenset(
    {"current", "pending_refresh", "partial", "stale", "unavailable", "live_fallback"}
)


def _invoke(workspace: Path) -> dict[str, object]:
    result = _runner.invoke(app, ["workspace-health", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    # The JSON payload is the final line; earlier lines may carry rich
    # display decorations from the CLI callback bootstrap.
    return json.loads(result.output.strip().splitlines()[-1])


@pytest.fixture
def fake_workspace() -> Path:
    return Path("/mock/workspace")


def test_workspace_health_routes_json_through_shared_display(
    fake_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command uses the shared display surface for operator output."""
    from ralph.diagnostics import workspace_health as command_module

    emitted: list[dict[str, object]] = []

    class _Display:
        def emit_json_payload(self, payload: dict[str, object]) -> None:
            emitted.append(payload)

    display_context = object()
    monkeypatch.setattr(
        command_module,
        "resolve_active_display",
        lambda _display, context: _Display() if context is display_context else None,
    )

    command_module._emit_workspace_health(str(fake_workspace), display_context=display_context)

    assert len(emitted) == 1
    assert emitted[0]["workspace"] == str(fake_workspace.absolute())


def test_workspace_health_reports_every_ac11_key(fake_workspace: Path) -> None:
    """The JSON payload contains every AC-11 key with real values."""
    payload = _invoke(fake_workspace)

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        assert key in payload, f"missing AC-11 key: {key}"

    storage = payload["storage"]
    assert isinstance(storage, list)
    categories = {row["category"] for row in storage}
    assert categories == set(_FIVE_CATEGORIES)
    for row in storage:
        assert isinstance(row["bytes"], int)
        assert isinstance(row["count"], int)
        assert row["purpose"]
        assert row["growth_trigger"]
        assert row["retention_basis"]

    freshness = payload["freshness"]
    assert freshness["token"] in (
        "current",
        "pending",
        "partial",
        "stale",
        "unavailable",
        "live_fallback",
    )
    assert "cause" in freshness
    assert isinstance(freshness["automatic_recovery"], bool)

    assert payload["readiness"] in _READINESS_TOKENS

    coverage_gaps = payload["coverage_gaps"]
    assert isinstance(coverage_gaps["unreadable_paths"], list)
    assert isinstance(coverage_gaps["unsupported_extensions"], list)

    assert isinstance(payload["active_observation"], list)
    assert isinstance(payload["active_refresh"], list)
    assert isinstance(payload["active_recovery"], list)

    active_cleanup = payload["active_cleanup"]
    assert isinstance(active_cleanup["candidate_count"], int)
    assert isinstance(active_cleanup["by_category"], dict)

    watch_capacity = payload["watch_capacity"]
    for field in ("mode", "cause", "automatic_recovery", "safe_next_action"):
        assert field in watch_capacity

    assert set(payload["cleanup_eligibility"]) == set(_FIVE_CATEGORIES)
    assert set(payload["recreatability"]) == set(_FIVE_CATEGORIES)
    assert all(isinstance(value, bool) for value in payload["recreatability"].values())


def test_workspace_health_is_read_only(fake_workspace: Path) -> None:
    """The health read must not create or delete workspace content."""
    payload = _invoke(fake_workspace)
    assert payload["workspace"] == str(fake_workspace.absolute())
    # No .agent directory is created by the read-only health command.
    assert not (fake_workspace / ".agent").exists()


def test_workspace_health_surfaces_live_fallback_watch_capacity(fake_workspace: Path) -> None:
    """A degraded observer surfaces through watch_capacity, not silently."""
    from ralph.workspace.awareness import (
        awareness_for_workspace,
        release_workspace_awareness,
    )

    awareness_for_workspace(fake_workspace).set_live_fallback("watch_capacity")
    try:
        payload = _invoke(fake_workspace)
    finally:
        release_workspace_awareness(fake_workspace)

    assert payload["freshness"]["token"] == "live_fallback"
    assert payload["readiness"] == "live_fallback"
    assert payload["watch_capacity"]["mode"] == "live_fallback"
    assert payload["watch_capacity"]["cause"] == "watch_capacity"
    assert payload["watch_capacity"]["automatic_recovery"] is True
    assert payload["watch_capacity"]["safe_next_action"]
