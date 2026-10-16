"""Tests for the durable three-state upstream tool catalog snapshot contract."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    load_upstream_tool_catalog,
    serialize_upstream_tool_catalog,
)
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.tool_catalog_cache import (
    UpstreamToolCatalogSnapshot,
    apply_tool_catalog_env,
    clear_tool_catalog,
    get_tool_catalog_snapshot,
    publish_tool_catalog_to_parent,
    read_snapshot_from_disk,
)


def _catalog() -> dict[str, list[UpstreamTool]]:
    return {
        "srv": [
            UpstreamTool(
                name="ping",
                description="Ping",
                input_schema={"type": "object"},
            )
        ]
    }


def _catalog_file(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp" / "parent_catalog.json"


class TestSnapshotContract:
    def test_snapshot_three_states_distinct(self) -> None:
        absent = UpstreamToolCatalogSnapshot(present=False)
        present_nonempty = UpstreamToolCatalogSnapshot(present=True, value=_catalog())
        present_empty = UpstreamToolCatalogSnapshot(present=True, value={})

        assert absent.present is False
        assert present_nonempty.present is True and present_nonempty.value
        assert present_empty.present is True and present_empty.value == {}
        # present-empty must not collapse into absent.
        assert present_empty.present is not absent.present

    def test_publish_writes_atomically(self, tmp_path: Path) -> None:
        publish_tool_catalog_to_parent(tmp_path, _catalog())

        target = _catalog_file(tmp_path)
        assert target.is_file()
        # No leftover tmp sibling.
        assert not (target.parent / "parent_catalog.json.tmp").exists()
        # Perms on POSIX.
        if os.name == "posix":
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        # Payload round-trips.
        assert load_upstream_tool_catalog(target.read_text(encoding="utf-8")) == _catalog()

    def test_publish_failure_removes_stale_file(self, tmp_path: Path) -> None:
        stale = _catalog_file(tmp_path)
        stale.parent.mkdir(parents=True)
        stale.write_text('{"old": []}', encoding="utf-8")

        # Make the atomic replace fail: the tmp write target is a directory.
        (stale.parent / "parent_catalog.json.tmp").mkdir()

        with pytest.raises(OSError):
            publish_tool_catalog_to_parent(tmp_path, _catalog())

        assert not stale.exists()

    def test_malformed_snapshot_fails_closed(self, tmp_path: Path) -> None:
        malformed = _catalog_file(tmp_path)
        malformed.parent.mkdir(parents=True)
        malformed.write_text("not json at all", encoding="utf-8")

        snapshot = read_snapshot_from_disk(tmp_path)
        assert snapshot.present is False
        assert snapshot.value == {}
        # Fail closed: the bad file is removed.
        assert not malformed.exists()

        # Non-mapping payload (valid JSON, wrong shape) also fails closed.
        malformed.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_snapshot_from_disk(tmp_path).present is False
        assert not malformed.exists()

        # Missing file is absent.
        assert get_tool_catalog_snapshot(tmp_path).present is False

    def test_session_plan_consumes_snapshot_via_presence_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env: dict[str, str] = {UPSTREAM_MCP_TOOL_CATALOG_ENV: "stale"}
        catalog = _catalog()
        publish_tool_catalog_to_parent(tmp_path, catalog)

        snapshot = get_tool_catalog_snapshot(tmp_path)
        assert snapshot.present is True
        apply_tool_catalog_env(env, snapshot)
        assert env[UPSTREAM_MCP_TOOL_CATALOG_ENV] == serialize_upstream_tool_catalog(catalog)

        clear_tool_catalog(tmp_path)
        snapshot_absent = get_tool_catalog_snapshot(tmp_path)
        assert snapshot_absent.present is False
        apply_tool_catalog_env(env, snapshot_absent)
        assert UPSTREAM_MCP_TOOL_CATALOG_ENV not in env

    def test_apply_tool_catalog_env_uses_presence_not_truthiness(self) -> None:
        # Present but empty: env var MUST be SET to a serialized empty mapping.
        env_present_empty: dict[str, str] = {}
        present_empty = UpstreamToolCatalogSnapshot(present=True, value={})
        apply_tool_catalog_env(env_present_empty, present_empty)
        assert UPSTREAM_MCP_TOOL_CATALOG_ENV in env_present_empty
        empty_payload = env_present_empty[UPSTREAM_MCP_TOOL_CATALOG_ENV]
        empty_decoded: object = json.loads(empty_payload)
        assert empty_decoded == {}

        # Round-trip: the set-but-empty env var decodes back to an empty catalog.
        assert load_upstream_tool_catalog(empty_payload) == {}

        # Absent: env var is POPped even if it held a value.
        env_absent: dict[str, str] = {UPSTREAM_MCP_TOOL_CATALOG_ENV: "{}"}
        apply_tool_catalog_env(env_absent, UpstreamToolCatalogSnapshot(present=False))
        assert UPSTREAM_MCP_TOOL_CATALOG_ENV not in env_absent

        # Present non-empty: env var is set to the serialized catalog.
        env_full: dict[str, str] = {}
        apply_tool_catalog_env(
            env_full, UpstreamToolCatalogSnapshot(present=True, value=_catalog())
        )
        assert env_full[UPSTREAM_MCP_TOOL_CATALOG_ENV] == serialize_upstream_tool_catalog(
            _catalog()
        )
